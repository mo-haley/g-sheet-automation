"""Tests for Phase 1 site control registry (merge, dedup, conflict detection).

Proves:
- Duplicates are merged conservatively
- Provenance is preserved
- Conflicting values surface as unresolved conflicts
- Missing/ambiguous fields produce warnings, not silence
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
from governing_docs.models import (
    ControlType,
    DiscoverySourceType,
    SiteControl,
)
from governing_docs.registry import build_registry

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"
_SAN_PEDRO_CACHE = _CACHE_DIR / "33_738650_-118_280925.json"


def _load_zimas_data(path: Path) -> dict:
    raw = json.loads(path.read_text())
    return raw.get("data", raw)


def _make_site(**kwargs):
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


def _make_parse_result(**kwargs):
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


# ============================================================
# Deduplication tests
# ============================================================

class TestRegistryDedup:
    """Test that duplicate discoveries from multiple sources merge correctly."""

    def test_d_limitation_deduped_across_sources(self):
        """D limitation discovered from both Site model and raw ZIMAS should merge to one."""
        site = _make_site(
            apn="7449-020-001",
            d_limitations=["Ord-XXXXX"],
            overlay_zones=["San Pedro CPIO"],
        )
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            supplemental_districts=["CPIO"],
        )
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)

        obs = []
        obs.extend(discover_from_site_model(site))
        obs.extend(discover_from_zoning_parse(parse, parcel_id="7449-020-001"))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id="7449-020-001"))

        registry = build_registry(obs, parcel_id="7449-020-001")

        d_controls = registry.get_controls_by_type(ControlType.D_LIMITATION)
        assert len(d_controls) == 1, (
            f"Expected 1 merged D limitation, got {len(d_controls)}: "
            f"{[c.raw_value for c in d_controls]}"
        )

    def test_cpio_deduped_across_sources(self):
        """CPIO from Site, parse, and raw ZIMAS should merge."""
        site = _make_site(
            apn="7449-020-001",
            overlay_zones=["CPIO"],
        )
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            supplemental_districts=["CPIO"],
        )
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)

        obs = []
        obs.extend(discover_from_site_model(site))
        obs.extend(discover_from_zoning_parse(parse, parcel_id="7449-020-001"))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id="7449-020-001"))

        registry = build_registry(obs, parcel_id="7449-020-001")

        cpio_controls = registry.get_controls_by_type(ControlType.CPIO)
        assert len(cpio_controls) == 1, (
            f"Expected 1 merged CPIO, got {len(cpio_controls)}"
        )

    def test_all_observations_preserved(self):
        """Even after dedup, all_observations should contain every raw observation."""
        site = _make_site(
            apn="test",
            d_limitations=["D"],
            overlay_zones=["CPIO"],
        )
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            supplemental_districts=["CPIO"],
        )

        obs = []
        obs.extend(discover_from_site_model(site))
        obs.extend(discover_from_zoning_parse(parse, parcel_id="test"))

        registry = build_registry(obs, parcel_id="test")
        assert len(registry.all_observations) == len(obs)


# ============================================================
# Provenance tests
# ============================================================

class TestRegistryProvenance:
    """Test that merged controls preserve source information."""

    def test_merged_control_has_resolution_notes(self):
        """When merging multiple sources, resolution_notes should indicate merge."""
        obs = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="Site.d_limitations[0]",
            ),
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="2D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="ZONE_CMPLT from layer 1102",
            ),
        ]
        registry = build_registry(obs)
        d_controls = registry.get_controls_by_type(ControlType.D_LIMITATION)
        assert len(d_controls) == 1
        assert "Merged from 2 sources" in d_controls[0].resolution_notes

    def test_raw_zimas_preferred_over_site_model(self):
        """Raw ZIMAS source should be canonical when merging with site model."""
        obs = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D-from-site",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="Site.d_limitations[0]",
            ),
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="2D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="ZONE_CMPLT from layer 1102",
            ),
        ]
        registry = build_registry(obs)
        d_controls = registry.get_controls_by_type(ControlType.D_LIMITATION)
        assert d_controls[0].source_type == DiscoverySourceType.RAW_ZIMAS_IDENTIFY


# ============================================================
# Conflict tests
# ============================================================

class TestRegistryConflicts:
    """Test that conflicting values are surfaced, not silently collapsed."""

    def test_conflicting_ordinance_numbers_create_conflict(self):
        obs = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="source-1",
                ordinance_number="Ord-111111",
            ),
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="source-2",
                ordinance_number="Ord-222222",
            ),
        ]
        registry = build_registry(obs)
        assert len(registry.conflicts) >= 1
        ord_conflicts = [c for c in registry.conflicts if c.field_name == "ordinance_number"]
        assert len(ord_conflicts) == 1
        assert set(ord_conflicts[0].values) == {"Ord-111111", "Ord-222222"}
        assert ord_conflicts[0].resolution == "unresolved"

    def test_conflicting_subareas_create_conflict(self):
        obs = [
            SiteControl(
                control_type=ControlType.CPIO,
                raw_value="CPIO",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="source-1",
                normalized_name="CPIO",
                subarea="A",
            ),
            SiteControl(
                control_type=ControlType.CPIO,
                raw_value="CPIO",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="source-2",
                normalized_name="CPIO",
                subarea="E",
            ),
        ]
        registry = build_registry(obs)
        sub_conflicts = [c for c in registry.conflicts if c.field_name == "subarea"]
        assert len(sub_conflicts) == 1
        assert registry.has_unresolved_conflicts


# ============================================================
# Warning tests
# ============================================================

class TestRegistryWarnings:
    """Test that missing/ambiguous data produces warnings."""

    def test_d_without_ordinance_warns(self):
        obs = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="2D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="test",
            ),
        ]
        registry = build_registry(obs)
        assert any("ordinance number" in w.lower() for w in registry.warnings)

    def test_cpio_without_subarea_warns(self):
        obs = [
            SiteControl(
                control_type=ControlType.CPIO,
                raw_value="CPIO",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="test",
                normalized_name="CPIO",
            ),
        ]
        registry = build_registry(obs)
        assert any("subarea" in w.lower() for w in registry.warnings)

    def test_empty_observations_produce_empty_registry(self):
        registry = build_registry([], parcel_id="test")
        assert len(registry.controls) == 0
        assert len(registry.conflicts) == 0
        assert len(registry.warnings) == 0


# ============================================================
# End-to-end: real parcel through full pipeline
# ============================================================

class TestEndToEndSanPedro:
    """Full discovery+registry pipeline using real San Pedro cached data."""

    def test_full_pipeline(self):
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)
        parcel_id = "33.738650_-118.280925"

        # Simulate what a real orchestrator would do
        site = _make_site(
            apn="7455-026-046",
            coordinates=(33.738650, -118.280925),
            d_limitations=[],  # normalizer extracts from string, not pre-populated
            overlay_zones=[],
        )
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            supplemental_districts=["CPIO"],
        )

        obs = []
        obs.extend(discover_from_site_model(site))
        obs.extend(discover_from_zoning_parse(parse, parcel_id=parcel_id))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id=parcel_id))

        registry = build_registry(obs, parcel_id=parcel_id)

        # Should have D + CPIO
        assert ControlType.D_LIMITATION in registry.control_types_present
        assert ControlType.CPIO in registry.control_types_present

        # Should NOT have Q or specific plan
        assert ControlType.Q_CONDITION not in registry.control_types_present
        assert ControlType.SPECIFIC_PLAN not in registry.control_types_present

        # All controls should require document resolution
        assert len(registry.documents_likely_required) >= 2

        # Warnings should mention missing ordinance/subarea
        assert len(registry.warnings) > 0
