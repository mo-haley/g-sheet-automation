"""Tests for control-to-authority linking.

Tests against:
1. Real San Pedro profile fixture (CPIO + D with multiple ordinances)
2. Synthetic profiles with known ambiguity patterns
3. Edge cases: no profile, empty profile, single ordinance, etc.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governing_docs.authority_links import build_profile_from_known_data
from governing_docs.discovery import discover_from_raw_zimas
from governing_docs.linker import link_registry
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    DiscoverySourceType,
    LinkConfidence,
    ParcelAuthorityItem,
    ParcelProfileData,
    SiteControl,
    SourceTier,
)
from governing_docs.parcel_profile_parser import parse_profile_response
from governing_docs.registry import build_registry

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_FIXTURE = _FIXTURE_DIR / "san_pedro_profile.html"
_ZIMAS_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"


@pytest.fixture()
def san_pedro_profile():
    return parse_profile_response(_SAN_PEDRO_FIXTURE.read_text())


@pytest.fixture()
def san_pedro_registry():
    zimas_data = json.loads(
        (_ZIMAS_CACHE_DIR / "33_738650_-118_280925.json").read_text()
    ).get("data", {})
    obs = discover_from_raw_zimas(zimas_data, parcel_id="7455026046")
    return build_registry(obs, parcel_id="7455026046")


# ============================================================
# CPIO linking — real San Pedro fixture
# ============================================================

class TestCPIOLinking:

    def test_cpio_has_deterministic_overlay_link(self, san_pedro_registry, san_pedro_profile):
        results = link_registry(san_pedro_registry, san_pedro_profile)
        cpio_results = [r for r in results if r.control.control_type == ControlType.CPIO]
        assert len(cpio_results) == 1

        cpio = cpio_results[0]
        det_links = [l for l in cpio.links if l.confidence == LinkConfidence.DETERMINISTIC]
        assert len(det_links) >= 1, "CPIO should have at least one deterministic link"

        # Check that the overlay link has the correct name
        overlay_links = [l for l in det_links if l.overlay_name]
        assert len(overlay_links) >= 1
        assert overlay_links[0].overlay_name == "San Pedro"

    def test_cpio_has_deterministic_zi_link(self, san_pedro_registry, san_pedro_profile):
        results = link_registry(san_pedro_registry, san_pedro_profile)
        cpio_results = [r for r in results if r.control.control_type == ControlType.CPIO]
        cpio = cpio_results[0]

        zi_links = [
            l for l in cpio.links
            if l.confidence == LinkConfidence.DETERMINISTIC and l.zi_code
        ]
        assert len(zi_links) >= 1
        assert zi_links[0].zi_code == "ZI-2478"

    def test_cpio_has_probable_sa_ordinance_link(self, san_pedro_registry, san_pedro_profile):
        results = link_registry(san_pedro_registry, san_pedro_profile)
        cpio_results = [r for r in results if r.control.control_type == ControlType.CPIO]
        cpio = cpio_results[0]

        probable_links = [l for l in cpio.links if l.confidence == LinkConfidence.PROBABLE]
        assert len(probable_links) >= 1
        assert probable_links[0].ordinance_number is not None
        # ORD-185541-SA135 has the SA suffix
        assert "SA" in (probable_links[0].linked_items[0].raw_text or "")

    def test_cpio_best_confidence_is_deterministic(self, san_pedro_registry, san_pedro_profile):
        results = link_registry(san_pedro_registry, san_pedro_profile)
        cpio_results = [r for r in results if r.control.control_type == ControlType.CPIO]
        assert cpio_results[0].best_confidence == LinkConfidence.DETERMINISTIC

    def test_cpio_subarea_from_overlay_link(self, san_pedro_registry, san_pedro_profile):
        results = link_registry(san_pedro_registry, san_pedro_profile)
        cpio = [r for r in results if r.control.control_type == ControlType.CPIO][0]
        overlay_links = [
            l for l in cpio.links
            if l.confidence == LinkConfidence.DETERMINISTIC and l.subarea
        ]
        assert len(overlay_links) >= 1
        assert overlay_links[0].subarea == "Regional Commercial"


# ============================================================
# D limitation linking — real San Pedro fixture
# ============================================================

class TestDLimitationLinking:

    def test_d_gets_candidate_set_not_deterministic(self, san_pedro_registry, san_pedro_profile):
        """D limitation should produce candidate_set (multiple non-SA ordinances)."""
        results = link_registry(san_pedro_registry, san_pedro_profile)
        d_results = [r for r in results if r.control.control_type == ControlType.D_LIMITATION]
        assert len(d_results) == 1

        d = d_results[0]
        # Should NOT be deterministic — no ZI item labels the D ordinance
        det_links = [l for l in d.links if l.confidence == LinkConfidence.DETERMINISTIC]
        assert len(det_links) == 0

    def test_d_candidate_set_excludes_sa_ordinances(self, san_pedro_registry, san_pedro_profile):
        """SA-suffixed ordinances should be excluded from D candidates."""
        results = link_registry(san_pedro_registry, san_pedro_profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_links = [l for l in d.links if l.confidence == LinkConfidence.CANDIDATE_SET]
        assert len(candidate_links) == 1

        for item in candidate_links[0].linked_items:
            assert "-SA" not in (item.raw_text or ""), \
                f"SA-suffixed ordinance {item.raw_text} should not be in D candidate set"

    def test_d_candidate_set_excludes_known_cpio_ord(self, san_pedro_registry, san_pedro_profile):
        """ORD-185539 is the known CPIO ordinance — should NOT be in D candidates."""
        results = link_registry(san_pedro_registry, san_pedro_profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_links = [l for l in d.links if l.confidence == LinkConfidence.CANDIDATE_SET]
        ord_nums = [i.ordinance_number for i in candidate_links[0].linked_items]
        assert "185539" not in ord_nums

    def test_d_candidate_set_has_multiple_candidates(self, san_pedro_registry, san_pedro_profile):
        """San Pedro has multiple non-SA ordinances → multiple D candidates."""
        results = link_registry(san_pedro_registry, san_pedro_profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_links = [l for l in d.links if l.confidence == LinkConfidence.CANDIDATE_SET]
        assert len(candidate_links[0].linked_items) > 1, \
            "Should have multiple D candidates (ambiguity is real)"

    def test_d_has_manual_review_warning(self, san_pedro_registry, san_pedro_profile):
        """Candidate set should carry a manual review warning."""
        results = link_registry(san_pedro_registry, san_pedro_profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_links = [l for l in d.links if l.confidence == LinkConfidence.CANDIDATE_SET]
        assert any("manual review" in w.lower() for w in candidate_links[0].warnings)


# ============================================================
# Synthetic: D with single non-SA ordinance → probable
# ============================================================

class TestDSingleOrdinance:

    def test_single_non_sa_ordinance_is_probable(self):
        """If only one non-SA ordinance on a D parcel, it's probable."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])

        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-999888",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="999888",
            ),
            ParcelAuthorityItem(
                raw_text="ORD-111222-SA5",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="111222",
            ),
        ]

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        probable_links = [l for l in d.links if l.confidence == LinkConfidence.PROBABLE]
        assert len(probable_links) == 1
        assert probable_links[0].ordinance_number == "999888"
        assert d.best_confidence == LinkConfidence.PROBABLE


# ============================================================
# Synthetic: D with ZI item → deterministic
# ============================================================

class TestDWithZIItem:

    def test_d_zi_item_is_deterministic(self):
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])

        profile = ParcelProfileData(source_method="test")
        zi_item = ParcelAuthorityItem(
            raw_text="ZI-9999 D Limitation Ord #555666",
            link_type=AuthorityLinkType.ZONING_INFORMATION,
            zi_code="ZI-9999",
            zi_title="D Limitation Ord #555666",
            mapped_control_type=ControlType.D_LIMITATION,
            ordinance_number="555666",
        )
        profile.zi_items = [zi_item]
        profile.authority_items = [zi_item]

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.DETERMINISTIC
        assert d.best_link.ordinance_number == "555666"
        assert d.best_link.zi_code == "ZI-9999"


# ============================================================
# Q condition linking
# ============================================================

class TestQConditionLinking:

    def test_q_with_no_q_zi_gets_candidate_set(self):
        control = SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])

        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-111111",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="111111",
            ),
            ParcelAuthorityItem(
                raw_text="ORD-222222",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="222222",
            ),
        ]

        results = link_registry(registry, profile)
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        assert q.best_confidence == LinkConfidence.CANDIDATE_SET
        assert len(q.best_link.linked_items) == 2

    def test_q_with_zi_is_deterministic(self):
        control = SiteControl(
            control_type=ControlType.Q_CONDITION,
            raw_value="Q",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])

        profile = ParcelProfileData(source_method="test")
        zi_item = ParcelAuthorityItem(
            raw_text="ZI-8888 Q Condition Ord #333444",
            link_type=AuthorityLinkType.ZONING_INFORMATION,
            zi_code="ZI-8888",
            zi_title="Q Condition Ord #333444",
            mapped_control_type=ControlType.Q_CONDITION,
            ordinance_number="333444",
        )
        profile.zi_items = [zi_item]
        profile.authority_items = [zi_item]

        results = link_registry(registry, profile)
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        assert q.best_confidence == LinkConfidence.DETERMINISTIC


# ============================================================
# Edge cases
# ============================================================

class TestLinkingEdgeCases:

    def test_no_profile_gives_unlinked(self):
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])
        results = link_registry(registry, None)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence == LinkConfidence.UNLINKED

    def test_empty_profile_gives_unlinked(self):
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])
        results = link_registry(registry, ParcelProfileData())
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence == LinkConfidence.UNLINKED

    def test_only_sa_ordinances_d_is_unlinked(self):
        """If all ordinances are SA-suffixed, D has no candidates."""
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        registry = build_registry([control])

        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-111111-SA5",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="111111",
            ),
        ]

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence == LinkConfidence.UNLINKED


# ============================================================
# End-to-end: linking → resolver integration
# ============================================================

class TestLinkingResolverIntegration:

    def test_d_candidate_set_surfaces_in_resolver_warnings(self, san_pedro_registry, san_pedro_profile):
        from governing_docs.resolver import resolve_registry

        result = resolve_registry(
            san_pedro_registry,
            community_plan_area="San Pedro",
            profile=san_pedro_profile,
        )

        d_res = result.get_by_type(ControlType.D_LIMITATION)
        assert len(d_res) == 1
        # Should have a warning about candidate ordinances
        all_warnings = " ".join(d_res[0].warnings)
        assert "candidate" in all_warnings.lower()

    def test_cpio_ordinance_linked_in_resolver(self, san_pedro_registry, san_pedro_profile):
        from governing_docs.resolver import resolve_registry

        result = resolve_registry(
            san_pedro_registry,
            community_plan_area="San Pedro",
            profile=san_pedro_profile,
        )

        cpio_res = result.get_by_type(ControlType.CPIO)
        assert cpio_res[0].ordinance_number is not None
        # Ordinance came from the probable SA-suffixed link
        all_warnings = " ".join(cpio_res[0].warnings)
        assert "probable" in all_warnings.lower()
