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


def _build_identify_params(x: float, y: float, layer_ids: list[int]) -> dict:
    """Build query params for the ZIMAS identify endpoint.

    Tolerance is in screen pixels; effective map tolerance = tolerance * (extent / imageDisplay).
    Nominatim geocodes to street centerlines, typically 15-30 ft from the parcel polygon.
    With extent_buffer=1000 and imageDisplay=600: 1 pixel ≈ 3.3 ft, so tolerance=10 ≈ 33 ft
    — enough to reach parcels from a street-center geocode without pulling in distant zones.
    """
    tolerance = 10
    extent_buffer = 1000
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


class ZIMASClient:
    """Client for the ZIMAS ArcGIS REST MapServer."""

    def __init__(self, cache: RawCache | None = None) -> None:
        self.base_url = ZIMAS_BASE_URL
        self.cache = cache or RawCache()
        self.pull_timestamp: str | None = None

    # Layers whose absence from a cached result triggers re-fetch.
    _CRITICAL_LAYERS = {ZIMAS_LAYERS["zoning"], ZIMAS_LAYERS["parcels"]}

    def _cache_has_critical_layers(self, data: dict) -> bool:
        """Check whether cached identify data contains results for critical layers."""
        result_layer_ids = {r.get("layerId") for r in data.get("results", [])}
        missing = self._CRITICAL_LAYERS - result_layer_ids
        if missing:
            layer_names = {v: k for k, v in ZIMAS_LAYERS.items()}
            names = [layer_names.get(lid, str(lid)) for lid in missing]
            logger.info("Cache miss on critical layers %s — will re-fetch", names)
            return False
        return True

    def identify(self, lat: float, lon: float) -> dict:
        """Run an identify query against all configured ZIMAS layers.

        Returns the raw JSON response dict. Results are cached locally.
        Stale cache entries missing critical layers (zoning, parcels) are
        automatically re-fetched.
        """
        x, y = latlon_to_stateplane(lat, lon)
        cache_key = f"{lat:.6f}_{lon:.6f}"

        cached = self.cache.get("zimas", cache_key)
        if cached:
            cached_data = cached.get("data", cached)
            if self._cache_has_critical_layers(cached_data):
                self.pull_timestamp = cached.get("pull_timestamp")
                return cached_data
            logger.info("Re-fetching ZIMAS data for %s (stale cache)", cache_key)

        layer_ids = list(ZIMAS_LAYERS.values())
        params = _build_identify_params(x, y, layer_ids)

        resp = requests.get(
            f"{self.base_url}/identify",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

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

        params = _build_identify_params(x, y, [layer_id])
        resp = requests.get(
            f"{self.base_url}/identify",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

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
        from parcel geometry.
        """
        layer_ids = list(ZIMAS_LAYERS.values())
        params = _build_identify_params(x, y, layer_ids)

        resp = requests.get(
            f"{self.base_url}/identify",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        self.pull_timestamp = datetime.now(timezone.utc).isoformat()
        return data
