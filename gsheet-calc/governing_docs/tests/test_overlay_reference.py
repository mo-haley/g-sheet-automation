"""Tests for overlay reference data and CPIO ordinance disambiguation.

Tests:
1. Static CPIO reference lookup
2. CPIO ordinance elimination from D candidate pool
3. San Pedro real fixture — 185539 now eliminated as known CPIO ordinance
4. Unknown districts return None
5. D provisions warning when CPIO ordinance is eliminated
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
)
from governing_docs.overlay_reference import (
    is_known_cpio_ordinance,
    lookup_cpio_ordinance,
)
from governing_docs.parcel_profile_parser import parse_profile_response
from governing_docs.registry import build_registry

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_FIXTURE = _FIXTURE_DIR / "san_pedro_profile.html"
_ZIMAS_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"


# ============================================================
# Static reference lookup tests
# ============================================================

class TestOverlayReference:

    def test_san_pedro_lookup(self):
        ref = lookup_cpio_ordinance("San Pedro")
        assert ref is not None
        assert ref.ordinance_number == "185539"
        assert ref.district_name == "San Pedro"

    def test_san_pedro_cpio_suffix(self):
        """Should match with or without 'CPIO' suffix."""
        ref = lookup_cpio_ordinance("San Pedro CPIO")
        assert ref is not None
        assert ref.ordinance_number == "185539"

    def test_case_insensitive(self):
        ref = lookup_cpio_ordinance("san pedro")
        assert ref is not None

    def test_downtown_no_ordinance(self):
        ref = lookup_cpio_ordinance("Downtown")
        assert ref is not None
        assert ref.ordinance_number is None  # Not listed on overlay page

    def test_unknown_district(self):
        ref = lookup_cpio_ordinance("Nonexistent District")
        assert ref is None

    def test_is_known_cpio_ordinance_185539(self):
        district = is_known_cpio_ordinance("185539")
        assert district == "San Pedro"

    def test_is_known_cpio_ordinance_unknown(self):
        assert is_known_cpio_ordinance("999999") is None


# ============================================================
# CPIO ordinance elimination in D disambiguation
# ============================================================

class TestCPIOOrdinanceElimination:

    def _d_control(self):
        return SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )

    def test_known_cpio_ord_eliminated_from_d_pool(self):
        """ORD-185539 (known San Pedro CPIO) should be eliminated from D candidates."""
        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-185539",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="185539",
            ),
            ParcelAuthorityItem(
                raw_text="ORD-179935",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="179935",
            ),
        ]

        registry = build_registry([self._d_control()])
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # 185539 eliminated as known CPIO → 179935 is the lone survivor → probable
        assert d.best_confidence == LinkConfidence.PROBABLE
        assert d.best_link.ordinance_number == "179935"

    def test_cpio_elimination_warning_mentions_embedded_provisions(self):
        """Warning should note that D provisions may be in the CPIO ordinance."""
        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-185539",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="185539",
            ),
            ParcelAuthorityItem(
                raw_text="ORD-179935",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="179935",
            ),
        ]

        registry = build_registry([self._d_control()])
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        all_warnings = " ".join(w for link in d.links for w in link.warnings)
        assert "cpio" in all_warnings.lower() or "embedded" in all_warnings.lower()

    def test_unknown_ord_not_eliminated(self):
        """Ordinances not in the reference should NOT be eliminated."""
        profile = ParcelProfileData(source_method="test")
        profile.authority_items = [
            ParcelAuthorityItem(
                raw_text="ORD-999999",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="999999",
            ),
            ParcelAuthorityItem(
                raw_text="ORD-888888",
                link_type=AuthorityLinkType.ORDINANCE,
                ordinance_number="888888",
            ),
        ]

        registry = build_registry([self._d_control()])
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # Both unknown → both remain as candidates
        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
        candidate_ords = {i.ordinance_number for i in d.best_link.linked_items}
        assert candidate_ords == {"999999", "888888"}


# ============================================================
# Real San Pedro fixture with CPIO elimination
# ============================================================

class TestSanPedroWithCPIOElimination:

    @pytest.fixture()
    def profile(self):
        return parse_profile_response(_SAN_PEDRO_FIXTURE.read_text())

    @pytest.fixture()
    def registry(self):
        zimas_data = json.loads(
            (_ZIMAS_CACHE_DIR / "33_738650_-118_280925.json").read_text()
        ).get("data", {})
        obs = discover_from_raw_zimas(zimas_data, parcel_id="test")
        return build_registry(obs, parcel_id="test")

    def test_185539_eliminated_from_d_candidates(self, registry, profile):
        """On real San Pedro data, ORD-185539 should now be eliminated."""
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # Get all candidate ordinances
        candidate_ords = set()
        for link in d.links:
            if link.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE):
                for item in link.linked_items:
                    if item.ordinance_number:
                        candidate_ords.add(item.ordinance_number)

        assert "185539" not in candidate_ords, \
            "ORD-185539 is the known San Pedro CPIO ordinance — should be eliminated"

    def test_san_pedro_d_candidate_count_reduced(self, registry, profile):
        """Before: 4 candidates. After CPIO elimination: 3 candidates."""
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_links = [
            l for l in d.links
            if l.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE)
        ]
        assert len(candidate_links) >= 1

        total_candidates = sum(len(l.linked_items) for l in candidate_links)
        # Was 4 (129944, 179935, 185539, 185540), now 3 (129944, 179935, 185540)
        assert total_candidates == 3

    def test_san_pedro_d_still_candidate_set(self, registry, profile):
        """Even with 185539 eliminated, 3 candidates remain → still candidate_set."""
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
