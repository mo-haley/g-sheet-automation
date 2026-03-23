"""Tests for Phase 1 site control discovery.

Uses real cached ZIMAS payloads and existing Site fixtures.
Tests that controls are discovered when present, raw values preserved,
and missing/ambiguous fields fail loud rather than disappearing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governing_docs.discovery import (
    discover_from_raw_zimas,
    discover_from_site_model,
    discover_from_zoning_parse,
)
from governing_docs.models import ControlType, DiscoverySourceType

# --- Path to real cached ZIMAS data ---
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"
_SAN_PEDRO_CACHE = _CACHE_DIR / "33_738650_-118_280925.json"  # C2-2D-CPIO
_DOWNTOWN_CACHE = _CACHE_DIR / "34_053696_-118_242921.json"  # [LF1-WH1-5][P2-FA][CPIO]


def _load_zimas_data(path: Path) -> dict:
    """Load a cached ZIMAS identify response."""
    raw = json.loads(path.read_text())
    return raw.get("data", raw)


# ============================================================
# Tests: discover_from_raw_zimas — San Pedro (C2-2D-CPIO)
# ============================================================

class TestRawZimasSanPedro:
    """San Pedro parcel: C2-2D-CPIO on layer 1102 (Chapter 1)."""

    @pytest.fixture()
    def controls(self):
        data = _load_zimas_data(_SAN_PEDRO_CACHE)
        return discover_from_raw_zimas(data, parcel_id="33.738650_-118.280925")

    def test_d_limitation_discovered(self, controls):
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        assert len(d_controls) >= 1, "D limitation should be discovered from '2D' in C2-2D-CPIO"

    def test_d_limitation_raw_value_preserved(self, controls):
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        # Raw value should contain the actual string from ZONE_CMPLT
        assert any("2D" in c.raw_value for c in d_controls)

    def test_d_limitation_source_is_raw_zimas(self, controls):
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        for dc in d_controls:
            assert dc.source_type == DiscoverySourceType.RAW_ZIMAS_IDENTIFY

    def test_d_limitation_requires_resolution(self, controls):
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        for dc in d_controls:
            assert dc.document_resolution_likely_required is True

    def test_cpio_discovered(self, controls):
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert len(cpio_controls) >= 1, "CPIO should be discovered from C2-2D-CPIO"

    def test_cpio_raw_value(self, controls):
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert any("CPIO" in c.raw_value.upper() for c in cpio_controls)

    def test_parcel_id_attached(self, controls):
        for c in controls:
            assert c.parcel_id == "33.738650_-118.280925"

    def test_layer_provenance(self, controls):
        for c in controls:
            assert c.zimas_layer_id == 1102
            assert c.zimas_layer_name is not None

    def test_no_q_condition(self, controls):
        """C2-2D-CPIO has no Q prefix."""
        q_controls = [c for c in controls if c.control_type == ControlType.Q_CONDITION]
        assert len(q_controls) == 0


# ============================================================
# Tests: discover_from_raw_zimas — Downtown (Chapter 1A)
# ============================================================

class TestRawZimasDowntown:
    """Downtown parcel: [LF1-WH1-5][P2-FA][CPIO] on layer 1101 (Chapter 1A)."""

    @pytest.fixture()
    def controls(self):
        data = _load_zimas_data(_DOWNTOWN_CACHE)
        return discover_from_raw_zimas(data, parcel_id="34.053696_-118.242921")

    def test_cpio_discovered_from_bracket_format(self, controls):
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert len(cpio_controls) >= 1, "CPIO should be discovered from [CPIO] bracket segment"

    def test_cpio_raw_value_preserves_bracket_content(self, controls):
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert any(c.raw_value == "CPIO" for c in cpio_controls)

    def test_layer_1101_provenance(self, controls):
        """Should come from Ch1A layer 1101."""
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        for c in cpio_controls:
            assert c.zimas_layer_id == 1101

    def test_no_d_limitation(self, controls):
        """This zoning string has no D suffix."""
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        assert len(d_controls) == 0

    def test_non_cpio_brackets_not_misclassified(self, controls):
        """[LF1-WH1-5] and [P2-FA] should NOT produce CPIO controls."""
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        for c in cpio_controls:
            assert "LF1" not in c.raw_value
            assert "P2" not in c.raw_value


# ============================================================
# Tests: discover_from_site_model
# ============================================================

class TestSiteModelDiscovery:
    """Test discovery from pre-built Site fixtures."""

    def _make_site(self, **kwargs):
        """Minimal Site-like object for testing without importing pydantic model."""
        class FakeSite:
            pass
        s = FakeSite()
        s.apn = kwargs.get("apn")
        s.coordinates = kwargs.get("coordinates")
        s.d_limitations = kwargs.get("d_limitations", [])
        s.q_conditions = kwargs.get("q_conditions", [])
        s.overlay_zones = kwargs.get("overlay_zones", [])
        s.specific_plan = kwargs.get("specific_plan")
        s.specific_plan_subarea = kwargs.get("specific_plan_subarea")
        return s

    def test_c2_2d_cpio_site(self):
        """Mirror of the TCC Beacon fixture: d_limitations + CPIO overlay."""
        site = self._make_site(
            apn="7449-020-001",
            d_limitations=["Ord-XXXXX"],
            overlay_zones=["San Pedro CPIO"],
        )
        controls = discover_from_site_model(site)

        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        assert len(d_controls) == 1
        assert d_controls[0].raw_value == "Ord-XXXXX"
        assert d_controls[0].source_type == DiscoverySourceType.SITE_MODEL
        assert any("placeholder" in w.lower() for w in d_controls[0].warnings)

        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert len(cpio_controls) == 1
        assert cpio_controls[0].normalized_name == "San Pedro CPIO"

    def test_site_with_specific_plan(self):
        site = self._make_site(
            apn="9999-001-001",
            specific_plan="Warner Center",
            specific_plan_subarea="B",
        )
        controls = discover_from_site_model(site)

        sp_controls = [c for c in controls if c.control_type == ControlType.SPECIFIC_PLAN]
        assert len(sp_controls) == 1
        assert sp_controls[0].subarea == "B"

    def test_empty_site_produces_no_controls(self):
        site = self._make_site()
        controls = discover_from_site_model(site)
        assert len(controls) == 0

    def test_q_condition_discovered(self):
        site = self._make_site(q_conditions=["Q-ORD-12345"])
        controls = discover_from_site_model(site)
        q_controls = [c for c in controls if c.control_type == ControlType.Q_CONDITION]
        assert len(q_controls) == 1
        assert q_controls[0].ordinance_number == "Q-ORD-12345"

    def test_parcel_id_from_apn(self):
        site = self._make_site(apn="1234-567-890", d_limitations=["D"])
        controls = discover_from_site_model(site)
        assert all(c.parcel_id == "1234-567-890" for c in controls)

    def test_parcel_id_from_coordinates_when_no_apn(self):
        site = self._make_site(coordinates=(34.05, -118.24), d_limitations=["D"])
        controls = discover_from_site_model(site)
        assert all(c.parcel_id == "34.050000_-118.240000" for c in controls)


# ============================================================
# Tests: discover_from_zoning_parse
# ============================================================

class TestZoningParseDiscovery:
    """Test discovery from ZoningParseResult-like objects."""

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

    def test_d_from_parse(self):
        pr = self._make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            D_ordinance_number="185539",
        )
        controls = discover_from_zoning_parse(pr, parcel_id="test")
        d_controls = [c for c in controls if c.control_type == ControlType.D_LIMITATION]
        assert len(d_controls) == 1
        assert d_controls[0].ordinance_number == "185539"

    def test_q_from_parse(self):
        pr = self._make_parse_result(
            raw_string="(Q)R4-1",
            has_Q_condition=True,
            Q_ordinance_number="ORD-12345",
        )
        controls = discover_from_zoning_parse(pr, parcel_id="test")
        q_controls = [c for c in controls if c.control_type == ControlType.Q_CONDITION]
        assert len(q_controls) == 1
        assert q_controls[0].ordinance_number == "ORD-12345"

    def test_t_from_parse(self):
        pr = self._make_parse_result(
            raw_string="[T][Q]RD1.5-1VL",
            has_T_classification=True,
            has_Q_condition=True,
        )
        controls = discover_from_zoning_parse(pr)
        t_controls = [c for c in controls if c.control_type == ControlType.T_CLASSIFICATION]
        assert len(t_controls) == 1

    def test_supplemental_cpio(self):
        pr = self._make_parse_result(
            raw_string="C2-2D-CPIO",
            supplemental_districts=["CPIO"],
        )
        controls = discover_from_zoning_parse(pr)
        cpio_controls = [c for c in controls if c.control_type == ControlType.CPIO]
        assert len(cpio_controls) == 1

    def test_no_controls_from_clean_zone(self):
        pr = self._make_parse_result(raw_string="R3-1")
        controls = discover_from_zoning_parse(pr)
        assert len(controls) == 0
