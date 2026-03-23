"""Authority-link harvesting and identifier extraction.

Parses structured items from ZIMAS parcel profile data to extract
D/Q/CPIO identifiers. No network fetching — operates on
ParcelProfileData that is either manually constructed or populated
by a future fetcher.

Key patterns:
  ZI items:       "ZI-2478 San Pedro CPO"
  Ordinances:     "Ord #185539" or "ORD-185539"
  Planning cases: "CPC-2009-2557-CPU"
  DIR:            "DIR-2020-2595-HCA-M1"
  CPIO overlay:   "San Pedro Community Plan Implementation Overlay District (CPIO)"
"""

from __future__ import annotations

import re

from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    ParcelAuthorityItem,
    ParcelProfileData,
    SourceTier,
)


# ── Regex patterns for identifier extraction ────────────────────────

_ZI_PATTERN = re.compile(
    r"^(ZI-\d+)\s+(.+)$",
    re.IGNORECASE,
)

_ORD_PATTERN = re.compile(
    r"(?:Ord\.?\s*#?\s*|ORD[- ]?)(\d{4,7})",
    re.IGNORECASE,
)

_CASE_PATTERN = re.compile(
    r"(CPC-\d{4}-\d{3,6}(?:-[A-Z0-9]+)*)",
    re.IGNORECASE,
)

_DIR_PATTERN = re.compile(
    r"(DIR-\d{4}-\d{3,6}(?:-[A-Z0-9]+)*)",
    re.IGNORECASE,
)

_CPIO_OVERLAY_PATTERN = re.compile(
    r"(.+?)\s*(?:Community\s+Plan\s+Implementation\s+Overlay\s+District|CPIO)",
    re.IGNORECASE,
)

_CPIO_ABBREV_PATTERN = re.compile(r"\(CPIO\)", re.IGNORECASE)

_SUBAREA_PATTERN = re.compile(
    r"""(?:Subarea|Sub-?area|Sub\s+Area)\s+["']?([A-Z0-9]+)["']?""",
    re.IGNORECASE,
)

# ZI items that are known to reference CPIOs
_ZI_CPIO_KEYWORDS = re.compile(
    r"\bCPIO\b|\bCPO\b|\bCommunity\s+Plan\s+(?:Implementation\s+)?Overlay\b",
    re.IGNORECASE,
)

# ZI items that may reference D limitations
_ZI_D_KEYWORDS = re.compile(
    r"\bD\s*Limitation\b|\bD\s*-?\s*Limit\b",
    re.IGNORECASE,
)

# ZI items that may reference Q conditions
_ZI_Q_KEYWORDS = re.compile(
    r"\bQ\s*Condition\b|\bQ\s*-?\s*Cond\b",
    re.IGNORECASE,
)


def classify_authority_item(raw_text: str) -> ParcelAuthorityItem:
    """Classify and extract identifiers from a single authority item text.

    This is the core parser. It takes raw text from a ZIMAS parcel profile
    authority item and returns a structured ParcelAuthorityItem.
    """
    text = raw_text.strip()
    item = ParcelAuthorityItem(
        raw_text=text,
        link_type=AuthorityLinkType.UNKNOWN,
    )

    # Try ZI pattern first — most common and most structured
    zi_match = _ZI_PATTERN.match(text)
    if zi_match:
        item.link_type = AuthorityLinkType.ZONING_INFORMATION
        item.zi_code = zi_match.group(1).upper()
        item.zi_title = zi_match.group(2).strip()
        _classify_zi_item(item)
        return item

    # Try ordinance pattern
    ord_match = _ORD_PATTERN.search(text)
    if ord_match:
        item.link_type = AuthorityLinkType.ORDINANCE
        item.ordinance_number = ord_match.group(1)
        _check_ordinance_control_type(item, text)
        return item

    # Try DIR pattern (before CPC since DIR is more specific)
    dir_match = _DIR_PATTERN.search(text)
    if dir_match:
        item.link_type = AuthorityLinkType.DIR_DETERMINATION
        item.dir_number = dir_match.group(1).upper()
        return item

    # Try CPC pattern
    case_match = _CASE_PATTERN.search(text)
    if case_match:
        item.link_type = AuthorityLinkType.PLANNING_CASE
        item.case_number = case_match.group(1).upper()
        return item

    # Try CPIO overlay full name
    cpio_match = _CPIO_OVERLAY_PATTERN.search(text)
    if cpio_match or _CPIO_ABBREV_PATTERN.search(text):
        item.link_type = AuthorityLinkType.OVERLAY_DISTRICT
        item.mapped_control_type = ControlType.CPIO
        if cpio_match:
            item.overlay_name = cpio_match.group(1).strip()
        item.overlay_abbreviation = "CPIO"
        # Check for subarea
        sub_match = _SUBAREA_PATTERN.search(text)
        if sub_match:
            item.subarea = sub_match.group(1).upper()
        return item

    # Fallback: check if it mentions specific plan
    if re.search(r"specific\s+plan", text, re.IGNORECASE):
        item.link_type = AuthorityLinkType.SPECIFIC_PLAN_REF
        item.specific_plan_name = text
        return item

    return item


def _classify_zi_item(item: ParcelAuthorityItem) -> None:
    """Further classify a ZI item and map to a ControlType if possible."""
    title = item.zi_title or ""

    if _ZI_CPIO_KEYWORDS.search(title):
        item.mapped_control_type = ControlType.CPIO
        item.overlay_abbreviation = "CPIO"
        # Extract community plan name from ZI title
        # "San Pedro CPO" -> overlay_name = "San Pedro"
        cpo_match = re.match(r"(.+?)\s+CPO\b", title, re.IGNORECASE)
        cpio_match = re.match(r"(.+?)\s+CPIO\b", title, re.IGNORECASE)
        if cpo_match:
            item.overlay_name = cpo_match.group(1).strip()
        elif cpio_match:
            item.overlay_name = cpio_match.group(1).strip()
        return

    if _ZI_D_KEYWORDS.search(title):
        item.mapped_control_type = ControlType.D_LIMITATION
        # Try to extract ordinance number from title
        ord_match = _ORD_PATTERN.search(title)
        if ord_match:
            item.ordinance_number = ord_match.group(1)
        return

    if _ZI_Q_KEYWORDS.search(title):
        item.mapped_control_type = ControlType.Q_CONDITION
        ord_match = _ORD_PATTERN.search(title)
        if ord_match:
            item.ordinance_number = ord_match.group(1)
        return


def _check_ordinance_control_type(item: ParcelAuthorityItem, text: str) -> None:
    """Try to determine which control type an ordinance reference relates to."""
    if _ZI_D_KEYWORDS.search(text):
        item.mapped_control_type = ControlType.D_LIMITATION
    elif _ZI_Q_KEYWORDS.search(text):
        item.mapped_control_type = ControlType.Q_CONDITION
    elif _ZI_CPIO_KEYWORDS.search(text):
        item.mapped_control_type = ControlType.CPIO


def parse_profile_items(
    raw_texts: list[str],
) -> list[ParcelAuthorityItem]:
    """Parse a list of raw authority item texts from a parcel profile."""
    return [classify_authority_item(text) for text in raw_texts]


def build_profile_from_known_data(
    parcel_id: str | None = None,
    address: str | None = None,
    zoning_string: str | None = None,
    specific_plan: str | None = None,
    overlay_district_texts: list[str] | None = None,
    zi_item_texts: list[str] | None = None,
    other_authority_texts: list[str] | None = None,
    source_method: str = "manual_entry",
) -> ParcelProfileData:
    """Construct a ParcelProfileData from known/screenshot data.

    This is the entry point for creating profile data from manually-observed
    ZIMAS parcel profile screenshots, before any automated fetcher exists.
    """
    profile = ParcelProfileData(
        parcel_id=parcel_id,
        address=address,
        zoning_string=zoning_string,
        specific_plan=specific_plan,
        source_method=source_method,
    )

    # Parse overlay district texts
    if overlay_district_texts:
        profile.overlay_districts = list(overlay_district_texts)
        for text in overlay_district_texts:
            item = classify_authority_item(text)
            if item.link_type != AuthorityLinkType.UNKNOWN:
                profile.authority_items.append(item)

    # Parse ZI items
    if zi_item_texts:
        for text in zi_item_texts:
            item = classify_authority_item(text)
            profile.zi_items.append(item)
            profile.authority_items.append(item)

    # Parse other authority texts
    if other_authority_texts:
        for text in other_authority_texts:
            item = classify_authority_item(text)
            profile.authority_items.append(item)

    return profile


def extract_identifiers_for_control(
    profile: ParcelProfileData,
    control_type: ControlType,
) -> dict:
    """Extract all identifiers relevant to a specific control type from a profile.

    Returns a dict with keys like:
      ordinance_number, cpio_name, subarea, zi_code, case_number, etc.
    Only includes non-None values.
    """
    identifiers: dict = {}
    relevant_items: list[ParcelAuthorityItem] = []

    for item in profile.authority_items:
        if item.mapped_control_type == control_type:
            relevant_items.append(item)

    if not relevant_items:
        return identifiers

    # Collect all found identifiers across relevant items
    for item in relevant_items:
        if item.ordinance_number and "ordinance_number" not in identifiers:
            identifiers["ordinance_number"] = item.ordinance_number
        if item.zi_code and "zi_code" not in identifiers:
            identifiers["zi_code"] = item.zi_code
        if item.zi_title and "zi_title" not in identifiers:
            identifiers["zi_title"] = item.zi_title
        if item.case_number and "case_number" not in identifiers:
            identifiers["case_number"] = item.case_number
        if item.overlay_name and "overlay_name" not in identifiers:
            identifiers["overlay_name"] = item.overlay_name
        if item.overlay_abbreviation and "overlay_abbreviation" not in identifiers:
            identifiers["overlay_abbreviation"] = item.overlay_abbreviation
        if item.subarea and "subarea" not in identifiers:
            identifiers["subarea"] = item.subarea
        if item.specific_plan_name and "specific_plan_name" not in identifiers:
            identifiers["specific_plan_name"] = item.specific_plan_name

    # Also check overlay_districts for CPIO-specific info
    if control_type == ControlType.CPIO:
        for overlay_text in profile.overlay_districts:
            item = classify_authority_item(overlay_text)
            if item.overlay_name and "overlay_full_name" not in identifiers:
                identifiers["overlay_full_name"] = item.overlay_name
            if item.subarea and "subarea" not in identifiers:
                identifiers["subarea"] = item.subarea

    identifiers["source_tier"] = SourceTier.ZIMAS_PARCEL_PROFILE.value
    identifiers["relevant_item_count"] = len(relevant_items)

    return identifiers
