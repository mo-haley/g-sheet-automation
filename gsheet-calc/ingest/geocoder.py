from __future__ import annotations

"""Geocoding abstraction with Nominatim default provider.

Abstracts geocoding behind a provider interface so it can be replaced later.
Uses caching and a custom identifying User-Agent.
"""

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests

from config.settings import GEOCODER_MIN_DELAY_SEC, GEOCODER_USER_AGENT, RAW_CACHE_DIR


class GeocoderProvider(ABC):
    """Abstract geocoder interface."""

    @abstractmethod
    def geocode(self, address: str) -> tuple[float, float] | None:
        """Return (latitude, longitude) for the given address, or None if not found."""


class NominatimProvider(GeocoderProvider):
    """Nominatim geocoder with rate limiting and caching."""

    BASE_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._cache_dir = RAW_CACHE_DIR / "geocode"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, address: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in address)
        return self._cache_dir / f"{safe_name[:100]}.json"

    def geocode(self, address: str) -> tuple[float, float] | None:
        """Geocode an address using Nominatim with caching and rate limiting."""
        cache_file = self._cache_path(address)
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            if data:
                return (float(data[0]["lat"]), float(data[0]["lon"]))
            return None

        # Rate limit
        elapsed = time.time() - self._last_request_time
        if elapsed < GEOCODER_MIN_DELAY_SEC:
            time.sleep(GEOCODER_MIN_DELAY_SEC - elapsed)

        resp = requests.get(
            self.BASE_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": GEOCODER_USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        self._last_request_time = time.time()

        results = resp.json()
        cache_file.write_text(json.dumps(results, indent=2))

        if results:
            return (float(results[0]["lat"]), float(results[0]["lon"]))
        return None


class Geocoder:
    """Geocoder facade that delegates to a pluggable provider."""

    def __init__(self, provider: GeocoderProvider | None = None) -> None:
        self.provider = provider or NominatimProvider()

    def geocode(self, address: str) -> tuple[float, float] | None:
        """Return (lat, lng) or None."""
        return self.provider.geocode(address)
