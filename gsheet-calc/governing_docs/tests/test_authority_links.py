"""Tests for authority-link harvesting and identifier extraction.

Tests the parsing of ZIMAS parcel profile authority items,
ZI code extraction, and profile-enhanced resolution.

Uses synthetic profile data constructed from the TCC Beacon screenshot
evidence (the only real parcel-profile data documented in the repo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governing_docs.authority_links import (
    build_profile_from_known_data,
    classify_authority_item,
    extract_identifiers_for_control,
    parse_profile_items,
)
from governing_docs.discovery import (
    discover_from_raw_zimas,
    discover_from_zoning_parse,
)
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    DiscoverySourceType,
    ParcelAuthorityItem,
    ParcelProfileData,
    ResolutionStatus,
    SiteControl,
    SourceTier,
)
from governing_docs.registry import build_registry
from governing_docs.resolver import resolve_registry

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"
_SAN_PEDRO_CACHE = _CACHE_DIR / "33_738650_-118_280925.json"


def _load_zimas_data(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return raw.get("data", raw)


# ============================================================
# classify_authority_item tests
# ============================================================

class TestClassifyAuthorityItem:

    def test_zi_cpio_item(self):
        """ZI-2478 San Pedro CPO should classify as ZI + map to CPIO."""
        item = classify_authority_item("ZI-2478 San Pedro CPO")
        assert item.link_type == AuthorityLinkType.ZONING_INFORMATION
        assert item.zi_code == "ZI-2478"
        assert item.zi_title == "San Pedro CPO"
        assert item.mapped_control_type == ControlType.CPIO
        assert item.overlay_name == "San Pedro"

    def test_zi_enterprise_zone(self):
        """ZI-2130 Harbor Gateway State Enterprise Zone — no specific control mapping."""
        item = classify_authority_item("ZI-2130 Harbor Gateway State Enterprise Zone")
        assert item.link_type == AuthorityLinkType.ZONING_INFORMATION
        assert item.zi_code == "ZI-2130"
        assert item.mapped_control_type is None

    def test_ordinance_with_hash(self):
        """Ord #185539 should extract ordinance number."""
        item = classify_authority_item("Ord #185539")
        assert item.link_type == AuthorityLinkType.ORDINANCE
        assert item.ordinance_number == "185539"

    def test_ordinance_with_dash(self):
        item = classify_authority_item("ORD-185539")
        assert item.link_type == AuthorityLinkType.ORDINANCE
        assert item.ordinance_number == "185539"

    def test_ordinance_with_d_context(self):
        """Ordinance with D limitation context text."""
        item = classify_authority_item("D Limitation - Ord #185539")
        assert item.link_type == AuthorityLinkType.ORDINANCE
        assert item.ordinance_number == "185539"
        assert item.mapped_control_type == ControlType.D_LIMITATION

    def test_planning_case(self):
        item = classify_authority_item("CPC-2009-2557-CPU")
        assert item.link_type == AuthorityLinkType.PLANNING_CASE
        assert item.case_number == "CPC-2009-2557-CPU"

    def test_dir_determination(self):
        item = classify_authority_item("DIR-2020-2595-HCA-M1")
        assert item.link_type == AuthorityLinkType.DIR_DETERMINATION
        assert item.dir_number == "DIR-2020-2595-HCA-M1"

    def test_cpio_full_overlay_name(self):
        """Full CPIO overlay district name from parcel profile."""
        text = "San Pedro Community Plan Implementation Overlay District (CPIO)"
        item = classify_authority_item(text)
        assert item.link_type == AuthorityLinkType.OVERLAY_DISTRICT
        assert item.mapped_control_type == ControlType.CPIO
        assert item.overlay_name == "San Pedro"
        assert item.overlay_abbreviation == "CPIO"

    def test_cpio_with_subarea(self):
        text = "San Pedro Community Plan Implementation Overlay District (CPIO) Subarea E"
        item = classify_authority_item(text)
        assert item.mapped_control_type == ControlType.CPIO
        assert item.subarea == "E"

    def test_unknown_item(self):
        item = classify_authority_item("Some random text")
        assert item.link_type == AuthorityLinkType.UNKNOWN
        assert item.mapped_control_type is None

    def test_zi_d_limitation(self):
        """ZI item that references a D limitation."""
        item = classify_authority_item("ZI-1234 D Limitation Ord #999888")
        assert item.link_type == AuthorityLinkType.ZONING_INFORMATION
        assert item.mapped_control_type == ControlType.D_LIMITATION
        assert item.ordinance_number == "999888"

    def test_zi_q_condition(self):
        item = classify_authority_item("ZI-5678 Q Condition Ord #111222")
        assert item.link_type == AuthorityLinkType.ZONING_INFORMATION
        assert item.mapped_control_type == ControlType.Q_CONDITION
        assert item.ordinance_number == "111222"


# ============================================================
# build_profile_from_known_data tests
# ============================================================

class TestBuildProfile:

    def _tcc_beacon_profile(self) -> ParcelProfileData:
        """Build profile from TCC Beacon screenshot data."""
        return build_profile_from_known_data(
            parcel_id="7449-020-001",
            address="155 W 6th St, San Pedro, CA 90731",
            zoning_string="C2-2D-CPIO",
            specific_plan="NONE",
            overlay_district_texts=[
                "San Pedro Community Plan Implementation Overlay District (CPIO)",
            ],
            zi_item_texts=[
                "ZI-2130 Harbor Gateway State Enterprise Zone",
                "ZI-2478 San Pedro CPO",
            ],
            source_method="manual_entry",
        )

    def test_profile_has_authority_items(self):
        profile = self._tcc_beacon_profile()
        assert profile.has_authority_items
        # 1 overlay + 2 ZI items = 3 authority items
        assert len(profile.authority_items) == 3

    def test_profile_zi_items(self):
        profile = self._tcc_beacon_profile()
        assert len(profile.zi_items) == 2

    def test_cpio_identifiers_extracted(self):
        profile = self._tcc_beacon_profile()
        ids = extract_identifiers_for_control(profile, ControlType.CPIO)
        assert ids.get("overlay_name") == "San Pedro"
        assert ids.get("overlay_abbreviation") == "CPIO"
        assert ids.get("zi_code") == "ZI-2478"
        assert ids.get("source_tier") == "zimas_parcel_profile"

    def test_cpio_overlay_full_name(self):
        profile = self._tcc_beacon_profile()
        ids = extract_identifiers_for_control(profile, ControlType.CPIO)
        assert ids.get("overlay_full_name") == "San Pedro"

    def test_no_d_identifiers_in_tcc_beacon(self):
        """TCC Beacon profile doesn't have D ordinance in the screenshot data."""
        profile = self._tcc_beacon_profile()
        ids = extract_identifiers_for_control(profile, ControlType.D_LIMITATION)
        assert ids.get("ordinance_number") is None
        assert ids.get("relevant_item_count", 0) == 0

    def test_specific_plan_none(self):
        profile = self._tcc_beacon_profile()
        assert profile.specific_plan == "NONE"


# ============================================================
# Profile-enhanced resolver tests
# ============================================================

class TestProfileEnhancedResolution:

    def _make_parse_result(self, **kwargs):
        class FakeParseResult:
            pass
        pr = FakeParseResult()
        pr.raw_string = kwargs.get("raw_string", "")
        pr.has_D_limitation = kwargs.get("has_D_limitation", False)
        pr.D_ordinance_number = kwargs.get("D_ordinance_number")
        pr.has_Q_condition = kwargs.get("has_Q_condition", False)
        pr.Q_ordinance_number = kwargs.get("Q_ordinance_number")
        pr.has_T_classification = kwargs.get("has_T_classification", False)
        pr.supplemental_districts = kwargs.get("supplemental_districts", [])
        return pr

    def test_cpio_upgraded_with_profile_name(self):
        """CPIO with bare 'CPIO' name should be upgraded by profile data."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        profile = build_profile_from_known_data(
            overlay_district_texts=[
                "San Pedro Community Plan Implementation Overlay District (CPIO)",
            ],
            zi_item_texts=["ZI-2478 San Pedro CPO"],
        )

        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area=None, profile=profile)

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert len(cpio_res) == 1
        # Should be identifier_partial (has name from profile, missing subarea)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert any("parcel profile" in w.lower() for w in cpio_res[0].warnings)

    def test_d_upgraded_with_profile_ordinance(self):
        """D limitation should be upgraded when profile provides ordinance number."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        profile = build_profile_from_known_data(
            zi_item_texts=["ZI-9999 D Limitation Ord #185539"],
        )

        registry = build_registry([control])
        result = resolve_registry(registry, profile=profile)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert d_res[0].ordinance_number == "185539"

    def test_d_without_profile_stays_identified_only(self):
        """D with no profile data stays at identified_only."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, profile=None)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].status == ResolutionStatus.IDENTIFIED_ONLY

    def test_profile_does_not_downgrade_existing_ordinance(self):
        """If control already has an ordinance number, profile shouldn't overwrite it."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            ordinance_number="Ord-185539",
        )
        # Profile has no D items
        profile = build_profile_from_known_data(
            zi_item_texts=["ZI-2478 San Pedro CPO"],
        )
        registry = build_registry([control])
        result = resolve_registry(registry, profile=profile)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].ordinance_number == "Ord-185539"  # Unchanged
        assert d_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL

    def test_cpio_profile_subarea(self):
        """CPIO with subarea from profile overlay text."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        profile = build_profile_from_known_data(
            overlay_district_texts=[
                'San Pedro Community Plan Implementation Overlay District (CPIO) Subarea E',
            ],
        )
        registry = build_registry([control])
        result = resolve_registry(registry, profile=profile)

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].subarea == "E"
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL

    def test_full_pipeline_san_pedro_with_profile(self):
        """Full pipeline: real ZIMAS cache + synthetic profile from screenshot."""
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)
        parse = self._make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            supplemental_districts=["CPIO"],
        )
        profile = build_profile_from_known_data(
            parcel_id="7449-020-001",
            zoning_string="C2-2D-CPIO",
            overlay_district_texts=[
                "San Pedro Community Plan Implementation Overlay District (CPIO)",
            ],
            zi_item_texts=[
                "ZI-2130 Harbor Gateway State Enterprise Zone",
                "ZI-2478 San Pedro CPO",
            ],
        )

        obs = []
        obs.extend(discover_from_zoning_parse(parse, parcel_id="7449-020-001"))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id="7449-020-001"))

        registry = build_registry(obs, parcel_id="7449-020-001")

        # Without profile: D=identified_only, CPIO=identifier_partial (inferred)
        result_no_profile = resolve_registry(registry, community_plan_area="San Pedro")
        d_no = result_no_profile.get_by_type(ControlType.D_LIMITATION)
        assert d_no[0].status == ResolutionStatus.IDENTIFIED_ONLY

        # With profile: D still identified_only (no D ord in this profile),
        # CPIO upgraded to identifier_partial with confirmed name from profile
        result_with_profile = resolve_registry(
            registry, community_plan_area="San Pedro", profile=profile
        )
        d_with = result_with_profile.get_by_type(ControlType.D_LIMITATION)
        assert d_with[0].status == ResolutionStatus.IDENTIFIED_ONLY  # No D ord in profile

        cpio_with = result_with_profile.get_by_type(ControlType.CPIO)
        assert cpio_with[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        # The profile confirms CPIO name rather than just inferring it
        assert any("parcel profile" in w.lower() for w in cpio_with[0].warnings)


# ============================================================
# Source hierarchy tests
# ============================================================

class TestSourceHierarchy:
    """Verify that profile data takes precedence but doesn't overclaim."""

    def test_profile_name_preferred_over_inference(self):
        """Profile-derived CPIO name should be used over community-plan inference."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        profile = build_profile_from_known_data(
            overlay_district_texts=[
                "San Pedro Community Plan Implementation Overlay District (CPIO)",
            ],
        )
        registry = build_registry([control])
        # Pass community_plan_area="Different Name" — profile should win
        result = resolve_registry(
            registry, community_plan_area="Different Name", profile=profile
        )
        cpio_res = result.get_by_type(ControlType.CPIO)
        # Should use profile name, not "Different Name CPIO"
        assert cpio_res[0].inferred_name is None  # Not inferred — obtained from profile
        assert any("San Pedro" in w for w in cpio_res[0].warnings)

    def test_profile_with_empty_authority_items_no_effect(self):
        """Empty profile should have no effect on resolution."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        profile = ParcelProfileData()  # Empty
        registry = build_registry([control])
        result = resolve_registry(registry, profile=profile)
        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].status == ResolutionStatus.IDENTIFIED_ONLY
