"""Tests for Phase 2A resolver: D/Q/CPIO resolution status.

Proves:
- Resolution never overclaims "resolved" when only partial identifiers exist
- Placeholder ordinance numbers are flagged as manual_review_required
- CPIO name is inferred from community plan area (with warning)
- Chapter 1A format triggers source_format_unreliable warning
- Missing identifiers are explicit in the missing list
- Real San Pedro parcel through full discovery+registry+resolver pipeline
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
    ControlResolution,
    ControlType,
    DiscoverySourceType,
    RegistryResolution,
    ResolutionStatus,
    SiteControl,
)
from governing_docs.registry import build_registry
from governing_docs.resolver import resolve_registry

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"
_SAN_PEDRO_CACHE = _CACHE_DIR / "33_738650_-118_280925.json"
_DOWNTOWN_CACHE = _CACHE_DIR / "34_053696_-118_242921.json"


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
# D Limitation resolver tests
# ============================================================

class TestDLimitationResolver:

    def test_d_with_no_ordinance_is_identified_only(self):
        """D discovered from ZONE_CMPLT with no ordinance number."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="ZONE_CMPLT from layer 1102",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert len(d_res) == 1
        assert d_res[0].status == ResolutionStatus.IDENTIFIED_ONLY
        assert "ordinance_number" in d_res[0].missing
        assert d_res[0].next_step is not None

    def test_d_with_real_ordinance_is_identifier_partial(self):
        """D with a real ordinance number — partial, not complete (still needs doc)."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="Site.d_limitations[0]",
            ordinance_number="Ord-185539",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert len(d_res) == 1
        assert d_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert "document_text" in d_res[0].missing
        assert "ordinance_number" not in d_res[0].missing
        assert d_res[0].ordinance_number == "Ord-185539"

    def test_d_with_placeholder_ordinance_is_manual_review(self):
        """Placeholder ordinance like 'Ord-XXXXX' must flag manual review."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="Site.d_limitations[0]",
            ordinance_number="Ord-XXXXX",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].status == ResolutionStatus.MANUAL_REVIEW_REQUIRED
        assert d_res[0].identifier_is_placeholder is True
        assert any("placeholder" in w.lower() for w in d_res[0].warnings)

    def test_d_never_claims_identifier_complete(self):
        """Even with an ordinance number, D is never identifier_complete
        because document_text is always needed."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            ordinance_number="Ord-185539",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)
        d_res = result.get_by_type(ControlType.D_LIMITATION)
        # D has only one identifier dimension (ordinance_number), so
        # with it present, best status is identifier_partial (still needs doc)
        assert d_res[0].status != ResolutionStatus.IDENTIFIER_COMPLETE


# ============================================================
# Q Condition resolver tests
# ============================================================

class TestQConditionResolver:

    def test_q_with_no_ordinance(self):
        control = SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        q_res = result.get_by_type(ControlType.Q_CONDITION)
        assert q_res[0].status == ResolutionStatus.IDENTIFIED_ONLY
        assert "ordinance_number" in q_res[0].missing

    def test_q_with_ordinance(self):
        control = SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.ZONING_PARSE_RESULT,
            source_detail="test",
            ordinance_number="ORD-12345",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        q_res = result.get_by_type(ControlType.Q_CONDITION)
        assert q_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL

    def test_q_with_placeholder(self):
        control = SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            ordinance_number="TBD",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)

        q_res = result.get_by_type(ControlType.Q_CONDITION)
        assert q_res[0].status == ResolutionStatus.MANUAL_REVIEW_REQUIRED


# ============================================================
# CPIO resolver tests
# ============================================================

class TestCPIOResolver:

    def test_cpio_bare_name_no_community_plan(self):
        """CPIO with only literal 'CPIO' as name and no community plan."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area=None)

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIED_ONLY
        assert "cpio_name" in cpio_res[0].missing
        assert "subarea" in cpio_res[0].missing

    def test_cpio_name_inferred_from_community_plan(self):
        """CPIO name should be inferred from community plan area."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area="San Pedro")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert cpio_res[0].inferred_name == "San Pedro CPIO"
        assert any("inferred" in w.lower() for w in cpio_res[0].warnings)

    def test_cpio_with_explicit_name_not_overridden(self):
        """If control already has a real CPIO name, don't overwrite it."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="San Pedro CPIO",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            normalized_name="San Pedro CPIO",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area="San Pedro")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        # Should NOT have "inferred" warning since name was already known
        assert cpio_res[0].inferred_name is None

    def test_cpio_with_name_and_subarea(self):
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="San Pedro CPIO",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            normalized_name="San Pedro CPIO",
            subarea="E",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area="San Pedro")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert "subarea" not in cpio_res[0].missing
        assert "ordinance_number" in cpio_res[0].missing

    def test_cpio_with_all_identifiers(self):
        """With name + subarea + ordinance, status should be identifier_complete."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="San Pedro CPIO",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            normalized_name="San Pedro CPIO",
            subarea="E",
            ordinance_number="185539",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area="San Pedro")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_COMPLETE
        assert "document_text" in cpio_res[0].missing  # Always missing in Phase 2A

    def test_cpio_never_resolved_beyond_identifier_complete(self):
        """Even with all identifiers, CPIO never claims more than identifier_complete
        in Phase 2A because we don't fetch/parse documents."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="San Pedro CPIO",
            source_type=DiscoverySourceType.SITE_MODEL,
            source_detail="test",
            normalized_name="San Pedro CPIO",
            subarea="E",
            ordinance_number="185539",
        )
        registry = build_registry([control])
        result = resolve_registry(registry)
        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status in (
            ResolutionStatus.IDENTIFIER_PARTIAL,
            ResolutionStatus.IDENTIFIER_COMPLETE,
        )


# ============================================================
# Chapter 1A format warning tests
# ============================================================

class TestChapter1AWarning:

    def test_ch1a_discovery_tags_format_warning(self):
        """Controls from layer 1101 should have Ch1A format warning in discovery."""
        data = _load_zimas_data(_DOWNTOWN_CACHE)
        controls = discover_from_raw_zimas(data, parcel_id="test")
        for c in controls:
            assert any("Chapter 1A" in w for w in c.warnings), (
                f"Control {c.control_type} from layer 1101 missing Ch1A warning"
            )

    def test_ch1a_resolver_marks_format_unreliable(self):
        """Resolver should mark source_format_unreliable for Ch1A controls."""
        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
            zimas_layer_id=1101,
            zimas_layer_name="Zoning (Chapter 1A)",
        )
        registry = build_registry([control])
        result = resolve_registry(registry, community_plan_area="Downtown")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].source_format_unreliable is True
        assert cpio_res[0].source_format_warning is not None
        assert "Chapter 1A" in cpio_res[0].source_format_warning

    def test_ch1_controls_not_flagged(self):
        """Controls from layer 1102 (Chapter 1) should NOT have Ch1A warning."""
        data = _load_zimas_data(_SAN_PEDRO_CACHE)
        controls = discover_from_raw_zimas(data, parcel_id="test")
        for c in controls:
            assert not any("Chapter 1A zoning layer" in w for w in c.warnings), (
                f"Control {c.control_type} from Ch1 layer 1102 wrongly tagged as Ch1A"
            )


# ============================================================
# RegistryResolution aggregate tests
# ============================================================

class TestRegistryResolution:

    def test_worst_status(self):
        """worst_status should return the least-resolved status."""
        controls = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="2D",
                source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
                source_detail="test",
            ),
            SiteControl(
                control_type=ControlType.CPIO,
                raw_value="San Pedro CPIO",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="test",
                normalized_name="San Pedro CPIO",
                subarea="E",
                ordinance_number="185539",
            ),
        ]
        registry = build_registry(controls)
        result = resolve_registry(registry, community_plan_area="San Pedro")

        # D has no ordinance → identified_only
        # CPIO has all identifiers → identifier_complete
        # worst should be identified_only
        assert result.worst_status == ResolutionStatus.IDENTIFIED_ONLY

    def test_needs_manual_review(self):
        controls = [
            SiteControl(
                control_type=ControlType.D_LIMITATION,
                raw_value="D",
                source_type=DiscoverySourceType.SITE_MODEL,
                source_detail="test",
                ordinance_number="Ord-XXXXX",
            ),
        ]
        registry = build_registry(controls)
        result = resolve_registry(registry)
        assert result.needs_manual_review is True

    def test_empty_registry_resolution(self):
        registry = build_registry([], parcel_id="test")
        result = resolve_registry(registry)
        assert len(result.resolutions) == 0
        assert result.worst_status is None
        assert result.needs_manual_review is False


# ============================================================
# End-to-end: real San Pedro parcel
# ============================================================

class TestEndToEndSanPedroResolution:
    """Full discovery → registry → resolver pipeline on real cached data."""

    def test_san_pedro_resolution(self):
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)
        parcel_id = "33.738650_-118.280925"

        # Simulate realistic inputs (D from zoning string, no ordinance from ZIMAS)
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            supplemental_districts=["CPIO"],
        )

        obs = []
        obs.extend(discover_from_zoning_parse(parse, parcel_id=parcel_id))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id=parcel_id))

        registry = build_registry(obs, parcel_id=parcel_id)
        result = resolve_registry(registry, community_plan_area="San Pedro")

        # D limitation: no ordinance from ZIMAS → identified_only
        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert len(d_res) == 1
        assert d_res[0].status == ResolutionStatus.IDENTIFIED_ONLY
        assert "ordinance_number" in d_res[0].missing

        # CPIO: name inferred from community plan, no subarea → identifier_partial
        cpio_res = result.get_by_type(ControlType.CPIO)
        assert len(cpio_res) == 1
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert cpio_res[0].inferred_name == "San Pedro CPIO"
        assert "subarea" in cpio_res[0].missing

        # Overall worst status should be identified_only (from D)
        assert result.worst_status == ResolutionStatus.IDENTIFIED_ONLY

        # Should have actionable next steps for both
        assert result.has_actionable_next_steps

    def test_san_pedro_with_fixture_enrichment(self):
        """Simulate what happens when fixture data provides more identifiers."""
        site = _make_site(
            apn="7449-014-013",
            d_limitations=["Ord-185539"],
            overlay_zones=["San Pedro CPIO"],
            specific_plan_subarea="E",
        )
        zimas_data = _load_zimas_data(_SAN_PEDRO_CACHE)
        parse = _make_parse_result(
            raw_string="C2-2D-CPIO",
            has_D_limitation=True,
            D_ordinance_number="185539",
            supplemental_districts=["CPIO"],
        )

        obs = []
        obs.extend(discover_from_site_model(site))
        obs.extend(discover_from_zoning_parse(parse, parcel_id="7449-014-013"))
        obs.extend(discover_from_raw_zimas(zimas_data, parcel_id="7449-014-013"))

        registry = build_registry(obs, parcel_id="7449-014-013")
        result = resolve_registry(registry, community_plan_area="San Pedro")

        # D: has ordinance from fixture → identifier_partial
        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert d_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL
        assert d_res[0].ordinance_number in ("Ord-185539", "185539")

        # CPIO: has name from fixture, no subarea on the CPIO control itself
        # (subarea is on specific_plan_subarea, not on overlay_zones)
        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].status == ResolutionStatus.IDENTIFIER_PARTIAL


# ============================================================
# End-to-end: Downtown (Chapter 1A)
# ============================================================

class TestEndToEndDowntownResolution:
    """Chapter 1A parcel: verify format warnings propagate through resolver."""

    def test_downtown_cpio_resolution_with_ch1a_warning(self):
        zimas_data = _load_zimas_data(_DOWNTOWN_CACHE)
        obs = discover_from_raw_zimas(zimas_data, parcel_id="34.053696_-118.242921")
        registry = build_registry(obs, parcel_id="34.053696_-118.242921")
        result = resolve_registry(registry, community_plan_area="Downtown")

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert len(cpio_res) == 1
        assert cpio_res[0].source_format_unreliable is True
        assert "Chapter 1A" in (cpio_res[0].source_format_warning or "")
        # CPIO name inferred from community plan
        assert cpio_res[0].inferred_name == "Downtown CPIO"
