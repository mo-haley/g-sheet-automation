from __future__ import annotations

"""Field normalization utilities for raw ZIMAS values."""

import re


def normalize_zone(raw_zone: str | None) -> str | None:
    """Normalize a raw zoning string to a standard zone code.

    Strips height district, qualifiers, and whitespace.
    Example: '[Q]C2-1VL-CDO' -> 'C2'
    """
    if not raw_zone:
        return None
    # Remove bracketed qualifiers like [Q], [T], [QC]
    cleaned = re.sub(r"\[.*?\]", "", raw_zone).strip()
    # Take the first segment before height district separator
    parts = cleaned.split("-")
    if parts:
        return parts[0].strip()
    return cleaned


def extract_height_district(raw_zone: str | None) -> str | None:
    """Extract height district from a zoning string.

    Example: 'C2-1VL-CDO' -> '1VL'
    """
    if not raw_zone:
        return None
    cleaned = re.sub(r"\[.*?\]", "", raw_zone).strip()
    parts = cleaned.split("-")
    if len(parts) >= 2:
        return parts[1].strip()
    return None


def extract_overlays(raw_zone: str | None) -> list[str]:
    """Extract overlay zone suffixes from a zoning string.

    Standard format: 'C2-1VL-CDO-RIO' -> ['CDO', 'RIO']

    Bracket-only format: '[LF1-WH1-5][P2-FA][CPIO]' -> ['LF1-WH1-5', 'P2-FA', 'CPIO']
    When the entire zone string is bracket-encoded (e.g. Arts District Specific Plan zones),
    bracket removal leaves an empty string. In that case, return all bracket contents as
    overlay/district candidates. Downstream classifiers distinguish CPIO from SP sub-zones.
    """
    if not raw_zone:
        return []
    cleaned = re.sub(r"\[.*?\]", "", raw_zone).strip()

    # Standard format: content remains after bracket removal
    if cleaned:
        parts = cleaned.split("-")
        if len(parts) > 2:
            return [p.strip() for p in parts[2:] if p.strip()]
        return []

    # Bracket-only format: cleaned is empty, extract bracket contents directly
    return [b.strip() for b in re.findall(r"\[([^\]]+)\]", raw_zone) if b.strip()]


def extract_q_conditions(raw_zone: str | None) -> list[str]:
    """Extract Q-condition qualifiers from a zoning string.

    Example: '[Q]C2-1VL' -> ['Q']
    """
    if not raw_zone:
        return []
    return re.findall(r"\[(Q[^]]*)\]", raw_zone)


def extract_d_limitations(raw_zone: str | None) -> list[str]:
    """Extract D-limitation qualifiers from a zoning string.

    Example: '[D]C2-1VL' -> ['D']
    """
    if not raw_zone:
        return []
    return re.findall(r"\[(D[^]]*)\]", raw_zone)


def infer_chapter(zone: str | None, layer_source: str | None = None) -> str:
    """Attempt to infer Chapter 1 vs Chapter 1A applicability.

    Returns 'chapter_1', 'chapter_1a', or 'unknown'.
    """
    if layer_source == "zoning_ch1a":
        return "chapter_1a"
    if layer_source == "zoning":
        return "chapter_1"
    return "unknown"
