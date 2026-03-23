from __future__ import annotations

"""ZIMAS ArcGIS REST API integration.

Queries the ZIMAS MapServer identify endpoint for zoning, land use,
overlays, and other parcel attributes at a given coordinate.
"""

from datetime import datetime, timezone

import requests
from pyproj import Transformer

from config.settings import ZIMAS_BASE_URL, ZIMAS_LAYERS, ZIMAS_SPATIAL_REF
from ingest.raw_cache import RawCache


# WGS84 -> CA State Plane Zone 5 (feet)
_transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{ZIMAS_SPATIAL_REF}", always_xy=True)


def latlon_to_stateplane(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS84 lat/lon to CA State Plane Zone 5 (feet)."""
    x, y = _transformer.transform(lon, lat)
    return (x, y)


def _build_identify_params(x: float, y: float, layer_ids: list[int]) -> dict:
    """Build query params for the ZIMAS identify endpoint."""
    tolerance = 2
    extent_buffer = 100
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

    def identify(self, lat: float, lon: float) -> dict:
        """Run an identify query against all configured ZIMAS layers.

        Returns the raw JSON response dict. Results are cached locally.
        """
        x, y = latlon_to_stateplane(lat, lon)
        cache_key = f"{lat:.6f}_{lon:.6f}"

        cached = self.cache.get("zimas", cache_key)
        if cached:
            self.pull_timestamp = cached.get("pull_timestamp")
            return cached.get("data", cached)

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
