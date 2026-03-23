"""Tests for document context assembly and section targeting.

Uses real San Pedro fixtures to test end-to-end context assembly,
plus synthetic cases for conflict detection and targeting edge cases.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governing_docs.document_context import (
    ContextField,
    ContextFieldConfidence,
    ContextFieldSource,
    DocumentContext,
    build_document_context,
)
from governing_docs.discovery import discover_from_raw_zimas
from governing_docs.linker import link_registry
from governing_docs.models import (
    ControlType,
    DiscoverySourceType,
    SiteControl,
)
from governing_docs.parcel_profile_parser import parse_profile_response
from governing_docs.registry import build_registry
from governing_docs.section_targeting import (
    MatchStrength,
    find_relevant_sections,
)
from governing_docs.zi_extractor import extract_zi_text

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_SAN_PEDRO_PROFILE = _FIXTURE_DIR / "san_pedro_profile.html"
_ZI_2478_PDF = _FIXTURE_DIR / "ZI2478.pdf"
_ZIMAS_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "ingest" / "raw_cache" / "zimas"


# ============================================================
# Context assembly — real San Pedro
# ============================================================

class TestContextAssemblyRealData:

    @pytest.fixture()
    def profile(self):
        return parse_profile_response(_SAN_PEDRO_PROFILE.read_text())

    @pytest.fixture()
    def registry(self):
        zimas_data = json.loads(
            (_ZIMAS_CACHE_DIR / "33_738650_-118_280925.json").read_text()
        ).get("data", {})
        return build_registry(
            discover_from_raw_zimas(zimas_data, parcel_id="test"),
            parcel_id="test",
        )

    @pytest.fixture()
    def zi_extractions(self):
        return [extract_zi_text(_ZI_2478_PDF)]

    @pytest.fixture()
    def cpio_control(self, registry):
        cpio = registry.get_controls_by_type(ControlType.CPIO)
        return cpio[0] if cpio else None

    @pytest.fixture()
    def cpio_link_result(self, registry, profile, zi_extractions):
        results = link_registry(registry, profile, zi_extractions=zi_extractions)
        for r in results:
            if r.control.control_type == ControlType.CPIO:
                return r
        return None

    def test_context_has_overlay_name(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.has_overlay_name
        assert "San Pedro" in ctx.overlay_name.value

    def test_context_has_subarea(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.has_subarea
        assert ctx.subarea.value == "Regional Commercial"

    def test_subarea_source_is_profile_direct(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.subarea.source == ContextFieldSource.PARCEL_PROFILE_DIRECT

    def test_context_has_ordinance_from_zi_header(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.has_ordinance
        assert ctx.ordinance_number.value == "185539"
        assert ctx.ordinance_number.source == ContextFieldSource.ZI_DOCUMENT_HEADER

    def test_context_has_zi_code(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.zi_code is not None
        assert "2478" in ctx.zi_code.value

    def test_context_has_zoning_string(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.zoning_string is not None
        assert ctx.zoning_string.value == "C2-2D-CPIO"

    def test_narrowing_strength_strong(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        assert ctx.narrowing_strength == "strong"

    def test_search_boost_terms(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        boost = ctx.search_boost_terms
        assert any("Regional Commercial" in t for t in boost)
        assert any("San Pedro" in t for t in boost)

    def test_section_targeting_hints(self, cpio_control, cpio_link_result, profile, zi_extractions):
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        hints = ctx.get_section_targeting_hints()
        assert any("Regional Commercial" in h for h in hints)
        assert any("San Pedro" in h for h in hints)

    def test_no_value_conflicts(self, cpio_control, cpio_link_result, profile, zi_extractions):
        """On consistent real data, the only 'conflict' should be format
        differences (e.g. 'ZI-2478' vs 'ZI2478'), not actual value disagreements."""
        ctx = build_document_context(
            control=cpio_control,
            link_result=cpio_link_result,
            profile=profile,
            zi_extractions=zi_extractions,
        )
        # zi_code format difference (ZI-2478 vs ZI2478) is expected
        real_conflicts = [c for c in ctx.conflicts if "zi_code" not in c]
        assert len(real_conflicts) == 0


# ============================================================
# Context assembly — minimal / missing data
# ============================================================

class TestContextMinimal:

    def test_empty_context(self):
        ctx = build_document_context()
        assert ctx.narrowing_strength == "none"
        assert len(ctx.search_boost_terms) == 0

    def test_control_only(self):
        control = SiteControl(
            control_type=ControlType.D_LIMITATION,
            raw_value="2D",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
        )
        ctx = build_document_context(control=control)
        assert ctx.control_type == ControlType.D_LIMITATION
        assert ctx.narrowing_strength == "none"


# ============================================================
# Conflict detection
# ============================================================

class TestConflictDetection:

    def test_ordinance_conflict_from_linker_vs_zi(self):
        """If linker says ORD-111 but ZI doc header says ORD-222, conflict is recorded."""
        from governing_docs.models import (
            AuthorityLink,
            ControlLinkResult,
            LinkConfidence,
            ParcelAuthorityItem,
            ParcelProfileData,
        )
        from governing_docs.zi_extractor import ZIExtractionResult, ExtractionQuality

        control = SiteControl(
            control_type=ControlType.CPIO,
            raw_value="CPIO",
            source_type=DiscoverySourceType.RAW_ZIMAS_IDENTIFY,
            source_detail="test",
            normalized_name="CPIO",
        )
        link_result = ControlLinkResult(
            control=control,
            links=[AuthorityLink(
                control_type=ControlType.CPIO,
                confidence=LinkConfidence.DETERMINISTIC,
                ordinance_number="111111",
                zi_code="ZI-9999",
            )],
        )
        profile = ParcelProfileData(source_method="test")
        zi_item = ParcelAuthorityItem(
            raw_text="ZI-9999 Test",
            link_type="zoning_information",
            zi_code="ZI-9999",
            mapped_control_type=ControlType.CPIO,
        )
        profile.zi_items = [zi_item]

        zi_ext = ZIExtractionResult(
            zi_code="ZI9999",
            source_path="/fake",
            quality=ExtractionQuality.GOOD,
            header_ordinance_number="222222",
        )

        ctx = build_document_context(
            control=control,
            link_result=link_result,
            profile=profile,
            zi_extractions=[zi_ext],
        )

        assert len(ctx.conflicts) > 0
        assert any("222222" in c for c in ctx.conflicts)


# ============================================================
# Section targeting
# ============================================================

class TestSectionTargeting:

    def test_exact_subarea_match(self):
        text = """
CENTRAL COMMERCIAL SUBAREAS
Development standards for Central Commercial areas.

REGIONAL COMMERCIAL SUBAREA
Development standards for Regional Commercial areas.
Maximum FAR: 4.0:1

INDUSTRIAL SUBAREAS
Development standards for Industrial areas.
"""
        ctx = DocumentContext()
        ctx.subarea = ContextField(value="Regional Commercial", source=ContextFieldSource.PARCEL_PROFILE_DIRECT, confidence=ContextFieldConfidence.HIGH)
        ctx.search_boost_terms = ["Regional Commercial"]

        result = find_relevant_sections(text, ctx, document_identifier="test")

        exact = [s for s in result.relevant_sections if s.match_strength == MatchStrength.EXACT]
        assert len(exact) >= 1
        assert "regional commercial" in exact[0].section_heading.lower()

    def test_different_subarea_excluded(self):
        text = """
CENTRAL COMMERCIAL SUBAREAS
Not for this parcel.

REGIONAL COMMERCIAL SUBAREA
This is the correct section.
"""
        ctx = DocumentContext()
        ctx.subarea = ContextField(value="Regional Commercial", source=ContextFieldSource.PARCEL_PROFILE_DIRECT, confidence=ContextFieldConfidence.HIGH)
        ctx.search_boost_terms = ["Regional Commercial"]

        result = find_relevant_sections(text, ctx, document_identifier="test")

        excluded = result.excluded_sections
        # Central Commercial should be excluded since we're looking for Regional Commercial
        assert any("central commercial" in (s.section_heading or "").lower() for s in excluded)

    def test_missing_subarea_warns(self):
        text = "This document has no subarea references at all."
        ctx = DocumentContext()
        ctx.subarea = ContextField(value="Nonexistent Subarea", source=ContextFieldSource.PARCEL_PROFILE_DIRECT, confidence=ContextFieldConfidence.HIGH)
        ctx.search_boost_terms = ["Nonexistent Subarea"]

        result = find_relevant_sections(text, ctx, document_identifier="test")

        assert any("not found" in w.lower() for w in result.warnings)

    def test_no_context_warns(self):
        text = "Some document text."
        ctx = DocumentContext()  # No narrowing context

        result = find_relevant_sections(text, ctx, document_identifier="test")

        assert ctx.narrowing_strength == "none"
        assert any("no narrowing" in w.lower() for w in result.warnings)

    def test_ambiguous_when_no_sections_match(self):
        text = """
SECTION A: Something
Unrelated content.

SECTION B: Something else
Also unrelated.
"""
        ctx = DocumentContext()
        ctx.subarea = ContextField(value="Missing Subarea", source=ContextFieldSource.PARCEL_PROFILE_DIRECT, confidence=ContextFieldConfidence.HIGH)
        ctx.search_boost_terms = ["Missing Subarea"]

        result = find_relevant_sections(text, ctx, document_identifier="test")

        assert result.is_ambiguous


# ============================================================
# End-to-end: real ZI-2478 text + real context
# ============================================================

class TestEndToEndZI2478Targeting:

    def test_zi2478_san_pedro_targeting(self):
        """Real ZI-2478 text should match 'San Pedro' and 'CPIO' terms."""
        zi_result = extract_zi_text(_ZI_2478_PDF)

        ctx = DocumentContext()
        ctx.overlay_name = ContextField(value="San Pedro", source=ContextFieldSource.PARCEL_PROFILE_DIRECT, confidence=ContextFieldConfidence.HIGH)
        ctx.search_boost_terms = ["San Pedro", "CPIO"]

        result = find_relevant_sections(
            zi_result.full_text, ctx, document_identifier="ZI2478"
        )

        assert len(result.relevant_sections) > 0
        # Should not warn about missing overlay
        assert not any("not found" in w.lower() for w in result.warnings
                       if "overlay" in w.lower())
