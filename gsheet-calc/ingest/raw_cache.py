from __future__ import annotations

"""Cache raw API responses locally with timestamps."""

import json
from datetime import datetime, timezone
from pathlib import Path

from config.settings import RAW_CACHE_DIR


class RawCache:
    """Stores and retrieves raw API response JSON files."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or RAW_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_path(self, source: str, key: str) -> Path:
        safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self.cache_dir / source / f"{safe_key[:120]}.json"

    def get(self, source: str, key: str) -> dict | None:
        """Retrieve cached response, or None if not cached."""
        path = self._make_path(source, key)
        if path.exists():
            return json.loads(path.read_text())
        return None

    def put(self, source: str, key: str, data: dict) -> Path:
        """Store a raw API response with a pull timestamp."""
        path = self._make_path(source, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "pull_timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "key": key,
            "data": data,
        }
        path.write_text(json.dumps(envelope, indent=2))
        return path

    def list_cached(self, source: str) -> list[Path]:
        """List all cached files for a given source."""
        source_dir = self.cache_dir / source
        if source_dir.exists():
            return sorted(source_dir.glob("*.json"))
        return []
