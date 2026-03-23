"""Parse ZIMAS parcel profile AJAX response into ParcelProfileData.

The ZIMAS parcel profile is returned from:
    https://zimas.lacity.org/map.aspx?pin={PIN}&ajax=yes

The response is a JavaScript object literal (not JSON) with keys:
    Address, Addresses, selectedPins, selectedAPN, divTab1..divTab11, divTab1200, divTab1300

Each divTab contains escaped HTML table fragments with DataCellsLeft/DataCellsRight
pairs. Key data locations:
    divTab3: Zoning string, ZI items, CPIO name/subarea, overlays, specific plans
    divTab5: Case numbers and ordinances

This parser extracts structured data without executing JavaScript.
"""

from __future__ import annotations

import re
from html import unescape as html_unescape

from governing_docs.authority_links import classify_authority_item
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    ParcelAuthorityItem,
    ParcelProfileData,
    SourceTier,
)


def parse_profile_response(raw_response: str, pin: str | None = None) -> ParcelProfileData:
    """Parse a raw ZIMAS map.aspx AJAX response into ParcelProfileData.

    Args:
        raw_response: The raw text response from map.aspx?pin=...&ajax=yes
        pin: Optional PIN for the parcel (also extracted from response).
    """
    profile = ParcelProfileData(
        source_method="zimas_ajax",
    )

    # Extract top-level fields
    profile.parcel_id = _extract_field(raw_response, "selectedAPN") or pin
    addr = _extract_field(raw_response, "Address")
    if addr:
        profile.address = addr

    # Extract and parse each relevant divTab
    tab3_html = _extract_tab_html(raw_response, "divTab3")
    tab5_html = _extract_tab_html(raw_response, "divTab5")

    if tab3_html:
        _parse_zoning_tab(tab3_html, profile)

    if tab5_html:
        _parse_cases_tab(tab5_html, profile)

    return profile


def _extract_field(raw: str, key: str) -> str | None:
    """Extract a simple string field from the JS object response."""
    pattern = rf'{key}:\s*"([^"]*)"'
    m = re.search(pattern, raw)
    return m.group(1) if m else None


def _extract_tab_html(raw: str, tab_name: str) -> str | None:
    """Extract and unescape HTML content from a divTab field."""
    # Find the tab start: divTabN: "..."
    marker = f'{tab_name}: "'
    start = raw.find(marker)
    if start < 0:
        return None
    start += len(marker)

    # Find the end — the closing " before the next key or end of object
    # The HTML content uses \" for escaped quotes and \> \< for escaped tags
    # We need to find the unescaped closing quote
    pos = start
    while pos < len(raw):
        if raw[pos] == '"' and (pos == 0 or raw[pos - 1] != '\\'):
            break
        pos += 1

    if pos >= len(raw):
        return None

    escaped_html = raw[start:pos]

    # Unescape: \> -> >, \< -> <, \" -> "
    html = escaped_html.replace('\\>', '>').replace('\\<', '<').replace('\\"', '"')
    return html


def _parse_zoning_tab(html: str, profile: ParcelProfileData) -> None:
    """Parse divTab3 for zoning, ZI items, CPIO, overlays."""

    # Extract zoning string
    zoning_match = re.search(
        r"openDataLink\('ZONING',\s*'([^']+)'\)", html
    )
    if zoning_match:
        profile.zoning_string = zoning_match.group(1)

    # Extract ZI items: openDataLink('ZONEINFO', 'ZI-NNNN ...')
    for m in re.finditer(
        r"openDataLink\('ZONEINFO',\s*'(ZI-\d+[^']*)'\)", html
    ):
        zi_text = m.group(1)
        item = classify_authority_item(zi_text)
        item.source_tier = SourceTier.ZIMAS_PARCEL_PROFILE
        item.url = f"https://zimas.lacity.org/documents/zoneinfo/{item.zi_code}.pdf" if item.zi_code else None
        profile.zi_items.append(item)
        profile.authority_items.append(item)

    # Extract CPIO name: openDataLink('CPIO', 'San Pedro')
    cpio_match = re.search(
        r"openDataLink\('CPIO',\s*'([^']+)'\)", html
    )
    if cpio_match:
        cpio_name = cpio_match.group(1)
        profile.overlay_districts.append(f"{cpio_name} CPIO")

        # Extract CPIO subarea: openDataLink('CPIO_SUBAREAS', 'Name;Subarea')
        subarea_match = re.search(
            r"openDataLink\('CPIO_SUBAREAS',\s*'([^']+)'\)", html
        )
        subarea = None
        if subarea_match:
            parts = subarea_match.group(1).split(';')
            if len(parts) >= 2:
                subarea = parts[1].strip()

        cpio_item = ParcelAuthorityItem(
            raw_text=f"{cpio_name} CPIO" + (f" Subarea {subarea}" if subarea else ""),
            link_type=AuthorityLinkType.OVERLAY_DISTRICT,
            source_tier=SourceTier.ZIMAS_PARCEL_PROFILE,
            mapped_control_type=ControlType.CPIO,
            overlay_name=cpio_name,
            overlay_abbreviation="CPIO",
            subarea=subarea,
        )
        profile.authority_items.append(cpio_item)

    # Extract specific plan: look for "Specific Plan Area" label
    sp_match = re.search(
        r'Specific Plan Area[^<]*</[^>]*>[^<]*<td[^>]*>([^<]+)</td>',
        html,
    )
    if sp_match:
        sp_val = sp_match.group(1).strip()
        profile.specific_plan = sp_val if sp_val.lower() != "none" else "NONE"


def _parse_cases_tab(html: str, profile: ParcelProfileData) -> None:
    """Parse divTab5 for ordinances and case numbers.

    ZIMAS link type codes observed in real data:
        50000 = Ordinances
        20000 = CPC (City Planning Commission) cases
        30000 = DIR (Director of Planning) determinations
        40000 = ENV (Environmental) cases
        90000 = ZA (Zoning Administrator) cases
        4000  = Administrative review cases
        CASE_NUM = Generic case reference
        ENV_CASE = Environmental case reference
    """
    # Extract ordinances: openDataLink('50000', 'ORD-NNNNNN')
    for m in re.finditer(
        r"openDataLink\('50000',\s*'(ORD-[^']+)'\)", html
    ):
        ord_text = m.group(1)
        item = classify_authority_item(ord_text)
        item.source_tier = SourceTier.ZIMAS_PARCEL_PROFILE
        profile.authority_items.append(item)

    # Extract case numbers from all known link type codes
    _CASE_CODES = r"CASE_NUM|ENV_CASE|4000|20000|30000|40000|90000"
    for m in re.finditer(
        rf"openDataLink\('(?:{_CASE_CODES})',\s*'([^']+)'\)", html
    ):
        case_text = m.group(1)
        item = classify_authority_item(case_text)
        item.source_tier = SourceTier.ZIMAS_PARCEL_PROFILE
        if item.link_type == AuthorityLinkType.UNKNOWN:
            item.link_type = AuthorityLinkType.PLANNING_CASE
            item.case_number = case_text
        profile.authority_items.append(item)
