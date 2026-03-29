from __future__ import annotations

"""ZIMAS ArcGIS REST API integration.

Queries the ZIMAS MapServer identify endpoint for zoning, land use,
overlays, and other parcel attributes at a given coordinate.
"""

from datetime import datetime, timezone

import requests
from pyproj import Transformer

import logging

from config.settings import ZIMAS_BASE_URL, ZIMAS_LAYERS, ZIMAS_SPATIAL_REF
from ingest.raw_cache import RawCache

logger = logging.getLogger(__name__)


# WGS84 -> CA State Plane Zone 5 (feet)
_transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{ZIMAS_SPATIAL_REF}", always_xy=True)


def latlon_to_stateplane(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS84 lat/lon to CA State Plane Zone 5 (feet)."""
    x, y = _transformer.transform(lon, lat)
    return (x, y)


def _build_identify_params(
    x: float, y: float, layer_ids: list[int],
    tolerance: int = 15, extent_buffer: int = 1000,
) -> dict:
    """Build query params for the ZIMAS identify endpoint.

    Tolerance is in screen pixels; effective map tolerance = tolerance * (extent / imageDisplay).
    Nominatim geocodes to street centerlines, typically 15-40 ft from the parcel polygon.
    Major arterials can be 80-100 ft wide (centerline 40-50 ft from parcel edge).

    Default: extent_buffer=1000, imageDisplay=600 → 1 pixel ≈ 3.3 ft.
      tolerance=15 → ~50 ft (covers most residential/collector streets).
    Retry:  tolerance=30 → ~100 ft (covers major arterials).
    """
    return {
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "sr": ZIMAS_SPATIAL_REF,
        "layers": f"all:{','.join(str(lid) for lid in layer_ids)}",
        "tolerance": tolerance,
        "mapExtent": f"{x - extent_buffer},{y - extent_buffer},{x + extent_buffer},{y + extent_buffer}",
        "imageDisplay": "600,600,96",
        "returnGeometry": "true",
        "f": "json",
    }


class IdentifyStatus:
    """Structured status from an identify call for downstream issue surfacing."""

    __slots__ = ("used_wide_tolerance", "critical_layers_resolved")

    def __init__(self) -> None:
        self.used_wide_tolerance: bool = False
        self.critical_layers_resolved: bool = True


class ZIMASClient:
    """Client for the ZIMAS ArcGIS REST MapServer."""

    def __init__(self, cache: RawCache | None = None) -> None:
        self.base_url = ZIMAS_BASE_URL
        self.cache = cache or RawCache()
        self.pull_timestamp: str | None = None
        self.identify_status: IdentifyStatus = IdentifyStatus()

    # Zoning can come from either Chapter 1 (1102) or Chapter 1A (1101).
    _ZONING_LAYERS = {ZIMAS_LAYERS["zoning"], ZIMAS_LAYERS["zoning_ch1a"]}
    _PARCEL_LAYER = ZIMAS_LAYERS["parcels"]

    # Standard tolerance (~50 ft) and wide retry (~100 ft).
    _TOLERANCE_STANDARD = 15
    _TOLERANCE_WIDE = 30

    def _has_critical_layers(self, data: dict) -> bool:
        """Check whether identify data contains results for critical layers.

        Requires parcels (layer 105) AND at least one zoning layer
        (1102 Chapter 1 or 1101 Chapter 1A).
        """
        result_layer_ids = {r.get("layerId") for r in data.get("results", [])}
        has_zoning = bool(self._ZONING_LAYERS & result_layer_ids)
        has_parcels = self._PARCEL_LAYER in result_layer_ids
        if not has_zoning or not has_parcels:
            missing = []
            if not has_zoning:
                missing.append("zoning (1101/1102)")
            if not has_parcels:
                missing.append("parcels (105)")
            logger.info("Missing critical layers: %s", missing)
            return False
        return True

    def _fetch_identify(
        self, x: float, y: float, layer_ids: list[int], tolerance: int,
    ) -> dict:
        """Execute one identify request against the ZIMAS MapServer."""
        params = _build_identify_params(x, y, layer_ids, tolerance=tolerance)
        resp = requests.get(
            f"{self.base_url}/identify",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def identify(self, lat: float, lon: float) -> dict:
        """Run an identify query against all configured ZIMAS layers.

        Returns the raw JSON response dict. Results are cached locally.
        Stale cache entries missing critical layers (zoning, parcels) are
        automatically re-fetched.

        Uses a two-pass strategy:
          1. Standard tolerance (~50 ft) — covers most residential streets.
          2. If critical layers (zoning, parcels) are missing, retries with
             wide tolerance (~100 ft) to reach parcels from arterial centerlines.
        """
        self.identify_status = IdentifyStatus()

        x, y = latlon_to_stateplane(lat, lon)
        cache_key = f"{lat:.6f}_{lon:.6f}"

        cached = self.cache.get("zimas", cache_key)
        if cached:
            cached_data = cached.get("data", cached)
            if self._has_critical_layers(cached_data):
                self.pull_timestamp = cached.get("pull_timestamp")
                return cached_data
            logger.info("Re-fetching ZIMAS data for %s (stale cache)", cache_key)

        layer_ids = list(ZIMAS_LAYERS.values())

        # Pass 1: standard tolerance
        data = self._fetch_identify(x, y, layer_ids, self._TOLERANCE_STANDARD)

        if not self._has_critical_layers(data):
            # Pass 2: wider tolerance for arterial-centerline geocodes
            self.identify_status.used_wide_tolerance = True
            logger.info(
                "Retrying ZIMAS identify with wide tolerance (%d px) for %s",
                self._TOLERANCE_WIDE, cache_key,
            )
            wide_data = self._fetch_identify(x, y, layer_ids, self._TOLERANCE_WIDE)

            if self._has_critical_layers(wide_data):
                data = wide_data
            else:
                # Use whichever response has more results
                if len(wide_data.get("results", [])) > len(data.get("results", [])):
                    data = wide_data
                self.identify_status.critical_layers_resolved = False
                logger.warning(
                    "ZIMAS identify missing critical layers even at wide tolerance for %s",
                    cache_key,
                )

        self.pull_timestamp = datetime.now(timezone.utc).isoformat()
        self.cache.put("zimas", cache_key, data)
        return data

    def query_layer(self, layer_id: int, lat: float, lon: float) -> dict:
        """Query a single ZIMAS layer at a point. Results are cached."""
        x, y = latlon_to_stateplane(lat, lon)
        cache_key = f"layer_{layer_id}_{lat:.6f}_{lon:.6f}"

        cached = self.cache.get("zimas", cache_key)
        if cached:
            return cached.get("data", cached)

        data = self._fetch_identify(x, y, [layer_id], self._TOLERANCE_STANDARD)

        self.cache.put("zimas", cache_key, data)
        return data

    def query_parcel_by_bpp(self, bpp: str) -> list[dict]:
        """Query Landbase layer 105 for parcel features matching a BPP (assessor parcel number).

        Returns a list of feature dicts with 'attributes' and 'geometry' keys,
        or an empty list if no features found.
        """
        cache_key = f"bpp_{bpp}"
        cached = self.cache.get("zimas", cache_key)
        if cached:
            return cached.get("data", {}).get("features", [])

        resp = requests.get(
            f"{self.base_url}/{ZIMAS_LAYERS['parcels']}/query",
            params={
                "where": f"BPP='{bpp}'",
                "outFields": "*",
                "returnGeometry": "true",
                "f": "json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        self.cache.put("zimas", cache_key, data)
        return data.get("features", [])

    def identify_at_stateplane(self, x: float, y: float) -> dict:
        """Run an identify query at state plane coordinates (no WGS84 conversion).

        Used for APN-based lookups where we already have state plane centroids
        from parcel geometry. Uses the same two-pass tolerance strategy as
        identify().
        """
        layer_ids = list(ZIMAS_LAYERS.values())
        data = self._fetch_identify(x, y, layer_ids, self._TOLERANCE_STANDARD)

        if not self._has_critical_layers(data):
            wide_data = self._fetch_identify(x, y, layer_ids, self._TOLERANCE_WIDE)
            if len(wide_data.get("results", [])) >= len(data.get("results", [])):
                data = wide_data

        self.pull_timestamp = datetime.now(timezone.utc).isoformat()
        return data
