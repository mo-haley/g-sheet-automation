"""ZIMAS parcel profile client.

Fetches parcel profile data from the ZIMAS AJAX endpoint:
    https://zimas.lacity.org/map.aspx?pin={PIN}&ajax=yes

Uses existing RawCache for caching. Respects rate limits.
"""

from __future__ import annotations

import time

import requests

from governing_docs.models import ParcelProfileData
from governing_docs.parcel_profile_parser import parse_profile_response
from ingest.raw_cache import RawCache


_ZIMAS_PROFILE_URL = "https://zimas.lacity.org/map.aspx"
_DEFAULT_USER_AGENT = "KFA-GSheet-Calc/1.0"
_DEFAULT_TIMEOUT = 30
_MIN_REQUEST_INTERVAL_SEC = 2.0  # Be respectful to city servers


class ParcelProfileClient:
    """Fetch and cache ZIMAS parcel profile data."""

    def __init__(
        self,
        cache: RawCache | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
        min_interval: float = _MIN_REQUEST_INTERVAL_SEC,
    ) -> None:
        self.cache = cache or RawCache()
        self.user_agent = user_agent
        self.min_interval = min_interval
        self._last_request_time: float = 0.0

    def get_profile(self, pin: str) -> ParcelProfileData:
        """Fetch parcel profile data for a PIN.

        Returns cached data if available. Otherwise fetches from ZIMAS,
        caches the raw response, and parses it.

        Args:
            pin: ZIMAS PIN number (e.g. "015B201   135" or "015B201+++135").
                 Spaces are converted to "+" for the URL.
        """
        cache_key = self._make_cache_key(pin)

        # Check cache first
        cached = self.cache.get("zimas_profile", cache_key)
        if cached:
            raw_html = cached.get("data", {}).get("raw_html", "")
            if raw_html:
                return parse_profile_response(raw_html, pin=pin)

        # Fetch from ZIMAS
        raw_html = self._fetch(pin)

        # Cache the raw response
        self.cache.put("zimas_profile", cache_key, {"raw_html": raw_html})

        return parse_profile_response(raw_html, pin=pin)

    def _fetch(self, pin: str) -> str:
        """Fetch raw HTML from ZIMAS with rate limiting."""
        # Rate limit
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        url_pin = pin.replace(" ", "+")
        resp = requests.get(
            _ZIMAS_PROFILE_URL,
            params={"pin": url_pin, "ajax": "yes"},
            headers={"User-Agent": self.user_agent},
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        self._last_request_time = time.time()
        return resp.text

    def _make_cache_key(self, pin: str) -> str:
        """Normalize PIN to a filesystem-safe cache key."""
        return pin.strip().replace(" ", "_").replace("+", "_")
