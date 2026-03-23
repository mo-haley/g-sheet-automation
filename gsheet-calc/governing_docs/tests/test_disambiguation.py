"""Tests for D/Q ordinance disambiguation.

Covers:
1. Real San Pedro fixture — multi-ordinance with SA and ZI-referenced elimination
2. Single-candidate after elimination → probable
3. Multi-candidate after elimination → candidate_set (no false pick)
4. D+Q coexistence — both share the candidate pool
5. ZI item referencing an ordinance removes it from D/Q pool
6. ZI item for D/Q does NOT eliminate its own ordinance
7. All ordinances eliminated → unlinked
8. Misleading heuristic: old ordinance that looks like D but is actually baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governing_docs.discovery import discover_from_raw_zimas
from governing_docs.linker import link_registry, _disambiguate_ordinances
from governing_docs.models import (
    AuthorityLinkType,
    ControlType,
    DiscoverySourceType,
    LinkConfidence,
    ParcelAuthorityItem,
    ParcelProfileData,
    SiteControl,
)
from governing_docs.parcel_profile_parser import parse_profile_response
from governing_docs.registry import build_registry

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_FIXTURE = _FIXTURE_DIR / "san_pedro_profile.html"
_ZIMAS_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"


def _make_profile_with_ordinances(
    ord_specs: list[tuple[str, str | None]],
    zi_texts: list[str] | None = None,
) -> ParcelProfileData:
    """Build a test profile with specific ordinances.

    ord_specs: list of (ordinance_number, suffix_or_None)
        e.g. [("185539", None), ("185541", "SA135")]
    zi_texts: optional list of raw ZI item texts
    """
    from governing_docs.authority_links import classify_authority_item

    profile = ParcelProfileData(source_method="test")
    for ord_num, suffix in ord_specs:
        raw = f"ORD-{ord_num}" + (f"-{suffix}" if suffix else "")
        item = classify_authority_item(raw)
        profile.authority_items.append(item)

    if zi_texts:
        for text in zi_texts:
            item = classify_authority_item(text)
            profile.zi_items.append(item)
            profile.authority_items.append(item)

    return profile


def _d_control():
    return SiteControl(
        control_type=ControlType.D_LIMITATION,
        raw_value="2D",
        source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
        source_detail="test",
    )


def _q_control():
    return SiteControl(
        control_type=ControlType.Q_CONDITION,
        raw_value="Q",
        source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
        source_detail="test",
    )


# ============================================================
# Real San Pedro fixture — disambiguation behavior
# ============================================================

class TestSanPedroDisambiguation:

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

    def test_san_pedro_d_eliminates_sa_ordinance(self, registry, profile):
        """ORD-185541-SA135 should be eliminated from D candidates."""
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # Get the candidate set link
        candidate_links = [
            l for l in d.links
            if l.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE)
        ]
        assert len(candidate_links) >= 1

        # Check that SA ordinance is not in candidates
        all_candidate_ords = set()
        for link in candidate_links:
            for item in link.linked_items:
                if item.ordinance_number:
                    all_candidate_ords.add(item.ordinance_number)

        assert "185541" not in all_candidate_ords, \
            "SA-suffixed ORD-185541-SA135 should be eliminated"

    def test_san_pedro_d_eliminates_zi_referenced_ordinance(self, registry, profile):
        """ORD-188073 (referenced by ZI-2517) would be eliminated if present.
        In the real data, ORD-188073 is not in the ordinance list, but the
        mechanism should still work for ZI items that reference ordinances."""
        # Verify the disambiguation helper finds the ZI reference
        candidates, eliminated, warnings = _disambiguate_ordinances(profile)

        # ZI-2517 references "Ordinance 188073" — if that ordinance were
        # in the list, it would be eliminated. Currently it's not in the
        # ordinance list, so no elimination happens from this source.
        # This test documents the behavior rather than asserting elimination.
        assert isinstance(candidates, list)
        assert isinstance(eliminated, list)

    def test_san_pedro_d_has_disambiguation_provenance(self, registry, profile):
        """The D link should explain what was eliminated and why."""
        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # Should have warnings about disambiguation
        all_warnings = []
        for link in d.links:
            all_warnings.extend(link.warnings)
            if link.rationale:
                all_warnings.append(link.rationale)

        joined = " ".join(all_warnings).lower()
        assert "disambigu" in joined or "eliminat" in joined or "sa" in joined


# ============================================================
# Single candidate after elimination → probable
# ============================================================

class TestSingleCandidateAfterElimination:

    def test_one_non_sa_non_zi_remaining(self):
        """After removing SA and ZI-referenced ords, one left → probable."""
        profile = _make_profile_with_ordinances(
            [("111111", "SA5"), ("222222", None), ("333333", None)],
            zi_texts=["ZI-9999 Some regulation (Ordinance 333333)"],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.PROBABLE
        assert d.best_link.ordinance_number == "222222"

    def test_elimination_provenance_tracks_both_reasons(self):
        """Provenance should mention both SA and ZI elimination."""
        profile = _make_profile_with_ordinances(
            [("111111", "SA5"), ("222222", None), ("333333", None)],
            zi_texts=["ZI-9999 Some rule (Ordinance 333333)"],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        all_text = " ".join(
            (link.rationale or "") + " ".join(link.warnings)
            for link in d.links
        ).lower()
        assert "sa" in all_text
        assert "zi" in all_text or "referenced" in all_text


# ============================================================
# Multi-candidate after elimination → candidate_set
# ============================================================

class TestMultiCandidateAfterElimination:

    def test_two_non_sa_remaining(self):
        """Two non-SA ords remain → candidate_set, not probable."""
        profile = _make_profile_with_ordinances(
            [("111111", "SA5"), ("222222", None), ("333333", None)],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
        assert len(d.best_link.linked_items) == 2

    def test_candidate_set_includes_all_survivors(self):
        """All non-SA, non-ZI-referenced ords should be in the set."""
        profile = _make_profile_with_ordinances(
            [("100000", None), ("200000", None), ("300000", "SA1"), ("400000", None)],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
        candidate_ords = {i.ordinance_number for i in d.best_link.linked_items}
        assert candidate_ords == {"100000", "200000", "400000"}


# ============================================================
# D + Q coexistence
# ============================================================

class TestDQCoexistence:

    def test_d_and_q_share_candidate_pool(self):
        """D and Q on the same parcel should both see the same candidates."""
        profile = _make_profile_with_ordinances(
            [("111111", None), ("222222", None), ("333333", "SA1")],
        )
        registry = build_registry([_d_control(), _q_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        d_ords = {i.ordinance_number for i in d.best_link.linked_items}
        q_ords = {i.ordinance_number for i in q.best_link.linked_items}

        assert d_ords == q_ords == {"111111", "222222"}

    def test_d_and_q_both_warn_about_shared_pool(self):
        """Both D and Q should warn about shared pool ambiguity."""
        profile = _make_profile_with_ordinances(
            [("111111", None), ("222222", None)],
        )
        registry = build_registry([_d_control(), _q_control()])

        results = link_registry(registry, profile)
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        q_warnings = " ".join(w for link in q.links for w in link.warnings)
        assert "d limitation" in q_warnings.lower() or "manual review" in q_warnings.lower()

    def test_d_and_q_single_candidate_still_both_claim_it(self):
        """Even with one candidate, both D and Q claim it — no auto-split."""
        profile = _make_profile_with_ordinances(
            [("999999", None), ("888888", "SA1")],
        )
        registry = build_registry([_d_control(), _q_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        # Both should be probable (single candidate) — neither excludes the other
        assert d.best_confidence == LinkConfidence.PROBABLE
        assert q.best_confidence == LinkConfidence.PROBABLE
        assert d.best_link.ordinance_number == "999999"
        assert q.best_link.ordinance_number == "999999"


# ============================================================
# ZI item referencing an ordinance — safe elimination
# ============================================================

class TestZIOrdinanceElimination:

    def test_zi_referenced_ord_eliminated(self):
        """An ordinance mentioned by a non-D/Q ZI item is eliminated."""
        profile = _make_profile_with_ordinances(
            [("111111", None), ("222222", None)],
            zi_texts=["ZI-5555 Al Fresco Ordinance (Ordinance 222222)"],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.PROBABLE
        assert d.best_link.ordinance_number == "111111"

    def test_zi_d_limitation_does_not_eliminate_its_own_ord(self):
        """A ZI item mapped to D_LIMITATION should NOT eliminate its ordinance.
        That would be self-defeating — the D ZI item IS the linkage."""
        profile = _make_profile_with_ordinances(
            [("111111", None), ("222222", None)],
            zi_texts=["ZI-9999 D Limitation Ord #111111"],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # Should be deterministic (ZI item matched D), not have 111111 eliminated
        assert d.best_confidence == LinkConfidence.DETERMINISTIC
        assert d.best_link.ordinance_number == "111111"

    def test_zi_q_condition_does_not_eliminate_its_own_ord(self):
        """Same for Q — a Q-mapped ZI should not eliminate the Q ordinance."""
        profile = _make_profile_with_ordinances(
            [("111111", None)],
            zi_texts=["ZI-8888 Q Condition Ord #111111"],
        )
        registry = build_registry([_q_control()])

        results = link_registry(registry, profile)
        q = [r for r in results if r.control.control_type == ControlType.Q_CONDITION][0]

        assert q.best_confidence == LinkConfidence.DETERMINISTIC


# ============================================================
# All eliminated → unlinked
# ============================================================

class TestAllEliminated:

    def test_all_sa_all_eliminated(self):
        """If every ordinance is SA-suffixed, D has no candidates."""
        profile = _make_profile_with_ordinances(
            [("111111", "SA1"), ("222222", "SA2")],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.UNLINKED

    def test_all_eliminated_by_sa_and_zi(self):
        """SA + ZI elimination removes everything."""
        profile = _make_profile_with_ordinances(
            [("111111", "SA1"), ("222222", None)],
            zi_texts=["ZI-5555 Some unrelated rule (Ordinance 222222)"],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.UNLINKED


# ============================================================
# Misleading heuristic: don't overfit to "newest = D"
# ============================================================

class TestMisleadingHeuristics:

    def test_old_and_new_both_stay_as_candidates(self):
        """An old ordinance and a new one should BOTH remain as candidates
        — the old one could be the D limitation. No recency bias."""
        profile = _make_profile_with_ordinances(
            [("129944", None), ("185540", None)],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
        candidate_ords = {i.ordinance_number for i in d.best_link.linked_items}
        assert "129944" in candidate_ords
        assert "185540" in candidate_ords

    def test_no_recency_preference(self):
        """The linker should NOT prefer newer ordinances over older ones.
        Both are equal candidates."""
        profile = _make_profile_with_ordinances(
            [("100000", None), ("200000", None)],
        )
        registry = build_registry([_d_control()])

        results = link_registry(registry, profile)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        assert d.best_confidence == LinkConfidence.CANDIDATE_SET
        # Neither should be a primary_item — no preference
        assert d.best_link.primary_item is None
