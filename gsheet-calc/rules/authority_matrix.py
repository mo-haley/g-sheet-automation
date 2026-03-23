"""Authority index loader and lookup."""

import json
from pathlib import Path

from config.settings import DATA_DIR
from models.authority import RuleAuthority


_AUTHORITY_CACHE: dict[str, RuleAuthority] | None = None


def load_authority_index() -> dict[str, RuleAuthority]:
    """Load and cache the authority index from data/authority_index.json."""
    global _AUTHORITY_CACHE
    if _AUTHORITY_CACHE is not None:
        return _AUTHORITY_CACHE

    path = DATA_DIR / "authority_index.json"
    raw = json.loads(path.read_text())
    authorities = {}
    for entry in raw.get("authorities", []):
        auth = RuleAuthority(
            id=entry["id"],
            topic=entry.get("topic", ""),
            source_type=entry.get("source_type", "unknown"),
            controlling_authority=entry.get("controlling_authority", ""),
            secondary_authorities=entry.get("secondary_authorities", []),
            jurisdiction_scope=entry.get("jurisdiction_scope", "la_city"),
            chapter_scope=entry.get("chapter_scope", "both"),
            effective_date=entry.get("effective_date"),
            superseded_date=entry.get("superseded_date"),
            confidence=entry.get("confidence", "medium"),
            human_review_required=entry.get("human_review_required", False),
            notes=entry.get("notes"),
        )
        authorities[auth.id] = auth
    _AUTHORITY_CACHE = authorities
    return authorities


def get_authority(authority_id: str) -> RuleAuthority | None:
    """Look up a single authority record by ID."""
    index = load_authority_index()
    return index.get(authority_id)
