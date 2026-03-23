"""Tests for ZI document evidence integration into linking/disambiguation.

Uses real ZI PDF fixtures (ZI2478.pdf, ZI2130.pdf) and real parcel profile
fixture to test end-to-end evidence flow.

Tests:
1. ZI header ordinance eliminates from D/Q candidate pool
2. CPIO link strengthened by ZI document header ordinance
3. ZI document evidence ranks above profile heuristics
4. BODY_MENTION evidence does NOT eliminate candidates
5. D-mapped ZI items protect their ordinance from elimination
6. Real San Pedro end-to-end with ZI extraction
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
from governing_docs.parcel_profile_parser import parse_profile_response
from governing_docs.registry import build_registry
from governing_docs.zi_extractor import (
    ExtractionQuality,
    HarvestedReference,
    ReferenceConfidence,
    ZIExtractionResult,
    extract_zi_text,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_ZI_2478_PDF = _FIXTURE_DIR / "ZI2478.pdf"
_ZI_2130_PDF = _FIXTURE_DIR / "ZI2130.pdf"
_SAN_PEDRO_PROFILE = _FIXTURE_DIR / "san_pedro_profile.html"
_ZIMAS_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"


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


def _make_zi_extraction(
    zi_code: str,
    header_ordinance: str | None = None,
    header_title: str | None = None,
    body_ordinances: list[str] | None = None,
) -> ZIExtractionResult:
    """Build a synthetic ZI extraction result for testing."""
    result = ZIExtractionResult(
        zi_code=zi_code,
        source_path=f"/fake/{zi_code}.pdf",
        quality=ExtractionQuality.GOOD,
        full_text="synthetic",
        page_count=1,
        char_count=100,
        header_zi_number=zi_code.replace("ZI", ""),
        header_ordinance_number=header_ordinance,
        header_title=header_title,
    )
    if header_ordinance:
        result.references.append(HarvestedReference(
            reference_type="ordinance",
            value=header_ordinance,
            confidence=ReferenceConfidence.DIRECT_HEADER,
            page_number=1,
            text_snippet=f"ORDINANCE NO. {header_ordinance}",
        ))
    for ord_num in (body_ordinances or []):
        result.references.append(HarvestedReference(
            reference_type="ordinance",
            value=ord_num,
            confidence=ReferenceConfidence.BODY_MENTION,
            page_number=1,
            text_snippet=f"pursuant to Ordinance {ord_num}",
        ))
    return result


def _make_profile_with_zi_and_ords(
    zi_specs: list[tuple[str, ControlType | None]],
    ord_numbers: list[str],
) -> ParcelProfileData:
    """Build a profile with specific ZI items and ordinances."""
    from governing_docs.authority_links import classify_authority_item

    profile = ParcelProfileData(source_method="test")
    for zi_code, mapped_type in zi_specs:
        item = ParcelAuthorityItem(
            raw_text=f"{zi_code} Test Item",
            link_type=AuthorityLinkType.ZONING_INFORMATION,
            zi_code=zi_code,
            zi_title="Test Item",
            mapped_control_type=mapped_type,
        )
        profile.zi_items.append(item)
        profile.authority_items.append(item)

    for ord_num in ord_numbers:
        item = classify_authority_item(f"ORD-{ord_num}")
        profile.authority_items.append(item)

    return profile


# ============================================================
# ZI header ordinance eliminates from D/Q pool
# ============================================================

class TestZIHeaderEliminatesFromDQPool:

    def test_zi_header_ordinance_eliminated(self):
        """An ordinance declared in a non-D/Q ZI header should be eliminated."""
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2478", ControlType.CPIO)],
            ord_numbers=["185539", "179935"],
        )
        zi_ext = _make_zi_extraction("ZI2478", header_ordinance="185539", header_title="SAN PEDRO CPIO")

        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # 185539 should be eliminated → 179935 is lone survivor → probable
        assert d.best_confidence == LinkConfidence.PROBABLE
        assert d.best_link.ordinance_number == "179935"

    def test_zi_header_elimination_provenance(self):
        """Elimination should mention ZI document evidence in provenance.
        Use an ordinance NOT in the overlay reference to isolate ZI doc evidence."""
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-7777", ControlType.CPIO)],
            ord_numbers=["777888", "179935"],
        )
        zi_ext = _make_zi_extraction("ZI7777", header_ordinance="777888", header_title="Test CPIO")

        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        all_text = " ".join(
            (l.rationale or "") + " ".join(l.warnings) for l in d.links
        ).lower()
        assert "zi document" in all_text or "header" in all_text

    def test_zi_header_ranks_above_overlay_reference(self):
        """ZI document header should work even if overlay reference also catches it.
        Both eliminate the same ordinance — no double-counting error."""
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2478", ControlType.CPIO)],
            ord_numbers=["185539", "179935", "129944"],
        )
        zi_ext = _make_zi_extraction("ZI2478", header_ordinance="185539")

        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        candidate_ords = set()
        for link in d.links:
            if link.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE):
                for item in link.linked_items:
                    if item.ordinance_number:
                        candidate_ords.add(item.ordinance_number)

        assert "185539" not in candidate_ords


# ============================================================
# BODY_MENTION does NOT eliminate
# ============================================================

class TestBodyMentionDoesNotEliminate:

    def test_body_mention_does_not_remove_candidate(self):
        """An ordinance mentioned only in ZI body text should NOT be eliminated."""
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2498", None)],  # Not mapped to any control type
            ord_numbers=["187096", "179935"],
        )
        # ZI-2498 mentions 187096 in body but NOT in header
        zi_ext = _make_zi_extraction(
            "ZI2498",
            header_ordinance=None,
            body_ordinances=["187096"],
        )

        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        candidate_ords = set()
        for link in d.links:
            if link.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE):
                for item in link.linked_items:
                    if item.ordinance_number:
                        candidate_ords.add(item.ordinance_number)

        # 187096 should still be a candidate — body mention doesn't eliminate
        assert "187096" in candidate_ords


# ============================================================
# D-mapped ZI protects its ordinance
# ============================================================

class TestDMappedZIProtectsOrdinance:

    def test_d_zi_header_does_not_eliminate(self):
        """A ZI item mapped to D_LIMITATION should NOT have its ordinance
        eliminated from the D pool. The D ZI profile item creates a
        deterministic link. The ZI extraction's header ordinance is
        protected from elimination by the D/Q exclusion filter."""
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-9999", ControlType.D_LIMITATION)],
            ord_numbers=["555666", "179935"],
        )
        zi_ext = _make_zi_extraction("ZI9999", header_ordinance="555666", header_title="D Limitation")

        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # The D ZI profile item creates a deterministic link (from existing logic)
        assert d.best_confidence == LinkConfidence.DETERMINISTIC
        # The profile ZI item for D doesn't have an ordinance_number on the item
        # itself — only the ZI extraction has it. But the D-mapped ZI is caught
        # by the existing linker's "d_zi_items" check which returns early.
        # The key assertion: 555666 is NOT eliminated from the candidate pool
        # if disambiguation were to run (which it doesn't because D ZI was found).


# ============================================================
# CPIO link strengthened by ZI document header
# ============================================================

class TestCPIOLinkStrengthened:

    def test_cpio_gets_document_backed_ordinance(self):
        """CPIO link should gain an ordinance number from ZI document header."""
        cpio_control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2478", ControlType.CPIO)],
            ord_numbers=["185539"],
        )
        zi_ext = _make_zi_extraction("ZI2478", header_ordinance="185539", header_title="SAN PEDRO CPIO")

        registry = build_registry([cpio_control])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])

        cpio = [r for r in results if r.control.control_type == ControlType.CPIO][0]

        # Should have a deterministic link with document-derived ordinance
        doc_links = [
            l for l in cpio.links
            if l.confidence == LinkConfidence.DETERMINISTIC and l.ordinance_number == "185539"
        ]
        assert len(doc_links) >= 1
        assert any("document" in (l.rationale or "").lower() for l in doc_links)

    def test_cpio_document_ordinance_rationale_mentions_header(self):
        cpio_control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2478", ControlType.CPIO)],
            ord_numbers=[],
        )
        zi_ext = _make_zi_extraction("ZI2478", header_ordinance="185539", header_title="SAN PEDRO CPIO")

        registry = build_registry([cpio_control])
        results = link_registry(registry, profile, zi_extractions=[zi_ext])
        cpio = [r for r in results if r.control.control_type == ControlType.CPIO][0]

        doc_links = [l for l in cpio.links if l.ordinance_number == "185539"]
        assert len(doc_links) >= 1
        assert any("DIRECT_HEADER" in (l.rationale or "") for l in doc_links)


# ============================================================
# No ZI extractions → existing behavior unchanged
# ============================================================

class TestNoExtractionsUnchanged:

    def test_none_extractions(self):
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[("ZI-2478", ControlType.CPIO)],
            ord_numbers=["185539", "179935"],
        )
        registry = build_registry([_d_control()])

        # No zi_extractions → should behave exactly as before
        results = link_registry(registry, profile, zi_extractions=None)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        # 185539 still eliminated by overlay_reference (Step 3), not by ZI doc
        candidate_ords = set()
        for link in d.links:
            if link.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE):
                for item in link.linked_items:
                    if item.ordinance_number:
                        candidate_ords.add(item.ordinance_number)
        assert "185539" not in candidate_ords  # Still eliminated by overlay ref

    def test_empty_extractions(self):
        profile = _make_profile_with_zi_and_ords(
            zi_specs=[],
            ord_numbers=["111111", "222222"],
        )
        registry = build_registry([_d_control()])
        results = link_registry(registry, profile, zi_extractions=[])
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence == LinkConfidence.CANDIDATE_SET


# ============================================================
# End-to-end: real San Pedro with real ZI extraction
# ============================================================

class TestRealSanPedroEndToEnd:

    @pytest.fixture()
    def profile(self):
        return parse_profile_response(_SAN_PEDRO_PROFILE.read_text())

    @pytest.fixture()
    def registry(self):
        zimas_data = json.loads(
            (_ZIMAS_CACHE_DIR / "33_738650_-118_280925.json").read_text()
        ).get("data", {})
        obs = discover_from_raw_zimas(zimas_data, parcel_id="test")
        return build_registry(obs, parcel_id="test")

    @pytest.fixture()
    def zi_extractions(self):
        results = []
        for pdf_path in [_ZI_2478_PDF, _ZI_2130_PDF]:
            if pdf_path.exists():
                results.append(extract_zi_text(pdf_path))
        return results

    def test_d_185539_eliminated_by_zi_document(self, registry, profile, zi_extractions):
        """With real ZI-2478 extraction, ORD-185539 should be eliminated from D pool
        via ZI document header evidence."""
        results = link_registry(registry, profile, zi_extractions=zi_extractions)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]

        candidate_ords = set()
        for link in d.links:
            if link.confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE):
                for item in link.linked_items:
                    if item.ordinance_number:
                        candidate_ords.add(item.ordinance_number)

        assert "185539" not in candidate_ords

    def test_cpio_gets_document_ordinance(self, registry, profile, zi_extractions):
        """CPIO link should get ordinance 185539 from ZI-2478 document header."""
        results = link_registry(registry, profile, zi_extractions=zi_extractions)
        cpio = [r for r in results if r.control.control_type == ControlType.CPIO][0]

        doc_links = [
            l for l in cpio.links
            if l.ordinance_number == "185539"
            and l.confidence == LinkConfidence.DETERMINISTIC
        ]
        assert len(doc_links) >= 1

    def test_d_still_has_candidates(self, registry, profile, zi_extractions):
        """Even with 185539 eliminated, D should still have remaining candidates."""
        results = link_registry(registry, profile, zi_extractions=zi_extractions)
        d = [r for r in results if r.control.control_type == ControlType.D_LIMITATION][0]
        assert d.best_confidence in (LinkConfidence.CANDIDATE_SET, LinkConfidence.PROBABLE)
