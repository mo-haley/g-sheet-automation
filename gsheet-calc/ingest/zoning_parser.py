"""LA City zoning string parser for FAR-relevant fields.

Parses raw ZIMAS zoning strings into structured components:
  [T/Q prefix] [Base Zone] - [Height District] [D suffix] - [Supplemental Use District]

Examples:
  C2-2D-CPIO  -> base=C2, hd=2, D=yes, supplemental=CPIO
  C2-1        -> base=C2, hd=1
  (Q)R4-1     -> Q=yes, base=R4, hd=1
  [T][Q]RD1.5-1VL -> T=yes, Q=yes, base=RD1.5, hd=1VL
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# Known base zones in priority order (longest match first to avoid partial matches)
_BASE_ZONES = sorted(
    [
        "RD1.5", "RD2", "RD3", "RD4", "RD5", "RD6",
        "RAS3", "RAS4",
        "R1", "R2", "R3", "R4", "R5",
        "C1.5", "C1", "C2", "C4", "C5", "CM",
        "M1", "M2", "M3",
        "PF", "OS", "A1", "A2", "RE9", "RE11", "RE15", "RE20", "RE40",
        "RS", "R1", "RU", "RZ", "RW1", "RW2",
    ],
    key=len,
    reverse=True,
)

# Height district suffixes (order matters for matching)
_HD_SUFFIXES = ["1VL", "1XL", "1SS", "1L", "1", "2", "3", "4"]

_ZONE_CLASS_MAP = {
    "R2": "residential",
    "R3": "residential",
    "R4": "residential",
    "R5": "residential",
    "RD1.5": "residential",
    "RD2": "residential",
    "RD3": "residential",
    "RD4": "residential",
    "RD5": "residential",
    "RD6": "residential",
    "RAS3": "residential",
    "RAS4": "residential",
    "R1": "residential",
    "C1": "commercial",
    "C1.5": "commercial",
    "C2": "commercial",
    "C4": "commercial",
    "C5": "commercial",
    "CM": "manufacturing",
    "M1": "manufacturing",
    "M2": "manufacturing",
    "M3": "manufacturing",
}


@dataclass
class ZoningParseResult:
    raw_string: str
    base_zone: str | None = None
    zone_class: str | None = None  # residential / commercial / manufacturing / other
    height_district: str | None = None  # e.g. "1", "1L", "1VL", "2"
    has_D_limitation: bool = False
    D_ordinance_number: str | None = None
    has_Q_condition: bool = False
    Q_ordinance_number: str | None = None
    has_T_classification: bool = False
    supplemental_districts: list[str] = field(default_factory=list)
    parse_confidence: str = "confirmed"  # confirmed / provisional / unresolved
    parse_issues: list[str] = field(default_factory=list)


def parse_zoning_string(
    raw: str,
    q_ordinances: list[str] | None = None,
    d_ordinances: list[str] | None = None,
) -> ZoningParseResult:
    """Parse an LA City zoning string into structured components.

    Args:
        raw: Raw zoning string from ZIMAS (e.g. "C2-2D-CPIO", "(Q)R4-1").
        q_ordinances: Known Q condition ordinance numbers from ZIMAS.
        d_ordinances: Known D limitation ordinance numbers from ZIMAS.
    """
    result = ZoningParseResult(raw_string=raw)

    if not raw or not raw.strip():
        result.parse_confidence = "unresolved"
        result.parse_issues.append("Empty zoning string")
        return result

    s = raw.strip().upper()

    # Step 1: Extract T classification prefix
    if s.startswith("[T]") or s.startswith("(T)"):
        result.has_T_classification = True
        s = s[3:]
    elif s.startswith("T"):
        # Bare T prefix before a bracket or zone — be cautious
        pass

    # Step 2: Extract Q condition prefix
    if s.startswith("[Q]") or s.startswith("(Q)"):
        result.has_Q_condition = True
        s = s[3:]
    elif s.startswith("Q"):
        # Bare Q is ambiguous — could be a Q prefix or part of zone name
        pass

    # Handle combined [T][Q] or [Q][T]
    if s.startswith("[T]") or s.startswith("(T)"):
        result.has_T_classification = True
        s = s[3:]
    if s.startswith("[Q]") or s.startswith("(Q)"):
        result.has_Q_condition = True
        s = s[3:]

    # Step 3: Split on hyphens for component extraction
    parts = [p.strip() for p in s.split("-") if p.strip()]
    if not parts:
        result.parse_confidence = "unresolved"
        result.parse_issues.append(f"Cannot parse zoning string: '{raw}'")
        return result

    # Step 4: Extract base zone from first part
    first = parts[0]
    matched_zone = None
    for zone in _BASE_ZONES:
        if first.startswith(zone):
            matched_zone = zone
            break

    if matched_zone:
        result.base_zone = matched_zone
        result.zone_class = _ZONE_CLASS_MAP.get(matched_zone, "other")
    else:
        result.parse_confidence = "unresolved"
        result.parse_issues.append(f"Unrecognized base zone in '{first}'")
        return result

    # Step 5: Extract height district
    # The HD can be in the first part after the zone, or in the second part
    remaining_first = first[len(matched_zone):]

    hd_found = False
    # Check remaining of first part (e.g. "R3" + "1" from "R31" — unlikely but handle)
    # More commonly, HD is the second part: "R3-1" -> parts = ["R3", "1"]
    if len(parts) >= 2:
        hd_candidate = parts[1]
        # Check for D suffix: "2D" -> hd="2", D=true
        d_match = re.match(r"^(\d+(?:VL|XL|SS|L)?)D?$", hd_candidate, re.IGNORECASE)
        if d_match:
            hd_raw = d_match.group(1).upper()
            # Normalize HD format
            for hd_suffix in _HD_SUFFIXES:
                if hd_raw == hd_suffix:
                    result.height_district = hd_suffix
                    hd_found = True
                    break
            if hd_candidate.upper().endswith("D") and not hd_candidate.upper().endswith("VL"):
                # Check it's actually a D suffix not part of "VLD" etc.
                stripped = hd_candidate.upper().rstrip("D")
                # Only set D if removing D leaves a valid HD
                for hd_suffix in _HD_SUFFIXES:
                    if stripped == hd_suffix:
                        result.has_D_limitation = True
                        result.height_district = hd_suffix
                        hd_found = True
                        break

    if not hd_found:
        result.parse_confidence = "provisional"
        result.parse_issues.append(
            "Height district not identified. FAR lookup requires height district."
        )

    # Step 6: Extract supplemental districts (CPIO, CDO, etc.)
    # These are typically the 3rd+ parts
    supplemental_start = 2
    for part in parts[supplemental_start:]:
        cleaned = part.strip().upper()
        # Skip if it looks like a height district we already processed
        if cleaned in ("D",):
            result.has_D_limitation = True
            continue
        if cleaned and not re.match(r"^\d+[A-Z]*$", cleaned):
            result.supplemental_districts.append(cleaned)

    # Step 7: Attach ordinance numbers
    if result.has_Q_condition and q_ordinances:
        result.Q_ordinance_number = q_ordinances[0] if len(q_ordinances) == 1 else ", ".join(q_ordinances)

    if result.has_D_limitation and d_ordinances:
        result.D_ordinance_number = d_ordinances[0] if len(d_ordinances) == 1 else ", ".join(d_ordinances)

    # Step 8: Flag missing ordinance lookups
    if result.has_Q_condition and not result.Q_ordinance_number:
        result.parse_issues.append("Q condition present but ordinance number not available from ZIMAS")

    if result.has_D_limitation and not result.D_ordinance_number:
        result.parse_issues.append("D limitation present but ordinance number not available from ZIMAS")

    return result
