"""Tests for parcel profile parser against real ZIMAS HTML fixture.

The fixture san_pedro_profile.html is a real response from:
    https://zimas.lacity.org/map.aspx?pin=015B201+++135&ajax=yes
saved on 2026-03-22.

These tests validate that the parser correctly extracts structured data
from the ZIMAS AJAX response format without any network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governing_docs.authority_links import extract_identifiers_for_control
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    ResolutionStatus,
    SourceTier,
)
from governing_docs.parcel_profile_parser import (
    parse_profile_response,
    _extract_field,
    _extract_tab_html,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_FIXTURE = _FIXTURE_DIR / "san_pedro_profile.html"


@pytest.fixture()
def raw_response():
    return _SAN_PEDRO_FIXTURE.read_text()


@pytest.fixture()
def profile(raw_response):
    return parse_profile_response(raw_response, pin="015B201   135")


# ============================================================
# Basic parsing tests
# ============================================================

class TestBasicParsing:

    def test_address_extracted(self, profile):
        assert profile.address is not None
        assert len(profile.address) > 0

    def test_parcel_id_extracted(self, profile):
        assert profile.parcel_id == "7455026046"  # selectedAPN

    def test_source_method(self, profile):
        assert profile.source_method == "zimas_ajax"

    def test_has_authority_items(self, profile):
        assert profile.has_authority_items
        assert len(profile.authority_items) > 0

    def test_zoning_string(self, profile):
        assert profile.zoning_string == "C2-2D-CPIO"


# ============================================================
# ZI item extraction tests
# ============================================================

class TestZIItems:

    def test_zi_items_found(self, profile):
        assert len(profile.zi_items) >= 5

    def test_zi_2478_cpio_san_pedro(self, profile):
        """ZI-2478 should be extracted and mapped to CPIO."""
        zi_2478 = [i for i in profile.zi_items if i.zi_code == "ZI-2478"]
        assert len(zi_2478) == 1
        item = zi_2478[0]
        assert item.mapped_control_type == ControlType.CPIO
        assert "San Pedro" in (item.zi_title or "")
        assert item.source_tier == SourceTier.ZIMAS_PARCEL_PROFILE

    def test_zi_2478_has_pdf_url(self, profile):
        zi_2478 = [i for i in profile.zi_items if i.zi_code == "ZI-2478"]
        assert zi_2478[0].url == "https://zimas.lacity.org/documents/zoneinfo/ZI-2478.pdf"

    def test_zi_2130_enterprise_zone(self, profile):
        zi_2130 = [i for i in profile.zi_items if i.zi_code == "ZI-2130"]
        assert len(zi_2130) == 1
        assert zi_2130[0].mapped_control_type is None  # Not a D/Q/CPIO

    def test_zi_2452_transit(self, profile):
        zi_2452 = [i for i in profile.zi_items if i.zi_code == "ZI-2452"]
        assert len(zi_2452) == 1


# ============================================================
# CPIO extraction tests
# ============================================================

class TestCPIOExtraction:

    def test_cpio_overlay_district(self, profile):
        assert any("San Pedro CPIO" in d for d in profile.overlay_districts)

    def test_cpio_authority_item(self, profile):
        cpio_items = [
            i for i in profile.authority_items
            if i.mapped_control_type == ControlType.CPIO
            and i.link_type == AuthorityLinkType.OVERLAY_DISTRICT
        ]
        assert len(cpio_items) >= 1
        cpio = cpio_items[0]
        assert cpio.overlay_name == "San Pedro"
        assert cpio.overlay_abbreviation == "CPIO"

    def test_cpio_subarea(self, profile):
        cpio_items = [
            i for i in profile.authority_items
            if i.mapped_control_type == ControlType.CPIO
            and i.link_type == AuthorityLinkType.OVERLAY_DISTRICT
        ]
        assert cpio_items[0].subarea == "Regional Commercial"

    def test_cpio_identifiers(self, profile):
        ids = extract_identifiers_for_control(profile, ControlType.CPIO)
        assert ids.get("overlay_name") == "San Pedro"
        assert ids.get("subarea") == "Regional Commercial"
        assert ids.get("zi_code") == "ZI-2478"


# ============================================================
# Ordinance extraction tests
# ============================================================

class TestOrdinanceExtraction:

    def test_ordinances_found(self, profile):
        ord_items = [
            i for i in profile.authority_items
            if i.link_type == AuthorityLinkType.ORDINANCE
        ]
        assert len(ord_items) >= 3

    def test_ord_185539_present(self, profile):
        """The D limitation ordinance should be extracted."""
        ord_items = [
            i for i in profile.authority_items
            if i.ordinance_number == "185539"
        ]
        assert len(ord_items) >= 1

    def test_ord_185541_sa135_present(self, profile):
        """CPIO ordinance with subarea suffix should be extracted."""
        ord_items = [
            i for i in profile.authority_items
            if i.raw_text and "185541-SA135" in i.raw_text
        ]
        assert len(ord_items) >= 1


# ============================================================
# Case number extraction tests
# ============================================================

class TestCaseExtraction:

    def test_cases_found(self, profile):
        case_items = [
            i for i in profile.authority_items
            if i.link_type == AuthorityLinkType.PLANNING_CASE
            or i.case_number is not None
        ]
        assert len(case_items) > 0

    def test_dir_case_found(self, profile):
        dir_items = [
            i for i in profile.authority_items
            if i.link_type == AuthorityLinkType.DIR_DETERMINATION
            or (i.case_number and i.case_number.startswith("DIR-"))
        ]
        # DIR-2020-2595 is in the data
        assert len(dir_items) >= 1


# ============================================================
# Specific plan tests
# ============================================================

class TestSpecificPlan:

    def test_specific_plan_none(self, profile):
        assert profile.specific_plan in ("NONE", "None", None)


# ============================================================
# Integration: profile -> resolver
# ============================================================

class TestProfileToResolver:
    """Verify that parsed profile data flows correctly into the resolver."""

    def test_resolver_uses_profile_cpio(self, profile):
        from governing_docs.discovery import discover_from_raw_zimas
        from governing_docs.registry import build_registry
        from governing_docs.resolver import resolve_registry
        import json

        # Load real ZIMAS identify data
        cache_path = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas" / "33_738650_-118_280925.json"
        zimas_data = json.loads(cache_path.read_text()).get("data", {})

        obs = discover_from_raw_zimas(zimas_data, parcel_id="7455026046")
        registry = build_registry(obs, parcel_id="7455026046")

        # Resolve WITH profile
        result = resolve_registry(
            registry,
            community_plan_area="San Pedro",
            profile=profile,
        )

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert len(cpio_res) == 1
        # With profile + linker: CPIO has name + subarea + probable ordinance (SA-suffixed)
        # → identifier_complete (or identifier_partial if ordinance not linked)
        assert cpio_res[0].status in (
            ResolutionStatus.IDENTIFIER_PARTIAL,
            ResolutionStatus.IDENTIFIER_COMPLETE,
        )
        assert cpio_res[0].subarea == "Regional Commercial"

    def test_resolver_d_limitation_still_needs_work(self, profile):
        """D limitation ordinance is in profile but not auto-linked to D control."""
        from governing_docs.discovery import discover_from_raw_zimas
        from governing_docs.registry import build_registry
        from governing_docs.resolver import resolve_registry
        import json

        cache_path = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas" / "33_738650_-118_280925.json"
        zimas_data = json.loads(cache_path.read_text()).get("data", {})

        obs = discover_from_raw_zimas(zimas_data, parcel_id="7455026046")
        registry = build_registry(obs, parcel_id="7455026046")

        result = resolve_registry(
            registry,
            community_plan_area="San Pedro",
            profile=profile,
        )

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert len(d_res) == 1
        # D limitation: the profile has ORD-185539 in the ordinances section,
        # but the ordinance items are not auto-mapped to D_LIMITATION by the
        # classify_authority_item parser (they lack "D Limitation" context text).
        # This is the correct conservative behavior — we don't guess which
        # ordinance is the D ordinance.
        # The D control stays at identified_only unless the ordinance text
        # explicitly mentions "D limitation".
        assert d_res[0].status in (
            ResolutionStatus.IDENTIFIED_ONLY,
            ResolutionStatus.IDENTIFIER_PARTIAL,
        )


# ============================================================
# Low-level extraction tests
# ============================================================

class TestLowLevelExtraction:

    def test_extract_field_address(self, raw_response):
        addr = _extract_field(raw_response, "Address")
        assert addr is not None

    def test_extract_field_apn(self, raw_response):
        apn = _extract_field(raw_response, "selectedAPN")
        assert apn == "7455026046"

    def test_extract_tab_html(self, raw_response):
        tab3 = _extract_tab_html(raw_response, "divTab3")
        assert tab3 is not None
        assert len(tab3) > 1000
        assert "ZONE" in tab3.upper() or "Zoning" in tab3

    def test_extract_tab5_html(self, raw_response):
        tab5 = _extract_tab_html(raw_response, "divTab5")
        assert tab5 is not None
        assert "ORD-" in tab5 or "Ordinance" in tab5
