"""Tests for targeting auditability, page-awareness, and conflict weakening.

Tests:
1. Context values used are recorded with provenance
2. Conflicting alternatives are visible in targeting output
3. Conflict weakening is flagged when subarea has alternatives
4. Exclusion reasons are explicit
5. Page numbers populated when page_texts provided
6. Ambiguous result when expected subarea missing
7. Real ZI-2478 with page-aware targeting
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governing_docs.document_context import (
    ContextField,
    ContextFieldAlternative,
    ContextFieldConfidence,
    ContextFieldSource,
    DocumentContext,
)
from governing_docs.section_targeting import (
    MatchStrength,
    find_relevant_sections,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_ZI_2478_PDF = _FIXTURE_DIR / "ZI2478.pdf"


def _make_context(
    subarea: str | None = None,
    subarea_source: ContextFieldSource = ContextFieldSource.PARCEL_PROFILE_DIRECT,
    subarea_alts: list[tuple[str, ContextFieldSource]] | None = None,
    overlay_name: str | None = None,
) -> DocumentContext:
    ctx = DocumentContext()
    if subarea:
        alts = []
        if subarea_alts:
            for val, src in subarea_alts:
                alts.append(ContextFieldAlternative(
                    value=val, source=src,
                    confidence=ContextFieldConfidence.MEDIUM,
                    reason_not_primary=f"Lower-priority than {subarea_source.value}",
                ))
        ctx.subarea = ContextField(
            value=subarea, source=subarea_source,
            confidence=ContextFieldConfidence.HIGH,
            source_detail="test",
            alternatives=alts,
        )
    if overlay_name:
        ctx.overlay_name = ContextField(
            value=overlay_name, source=ContextFieldSource.PARCEL_PROFILE_DIRECT,
            confidence=ContextFieldConfidence.HIGH,
        )
    ctx.search_boost_terms = [t for t in [subarea, overlay_name] if t]
    return ctx


_MULTI_SUBAREA_DOC = """
CENTRAL COMMERCIAL SUBAREAS
Height limit: 45 feet.
FAR: 1.5:1

REGIONAL COMMERCIAL SUBAREA
Height limit: unlimited.
FAR: 4.0:1
Maximum density: per CPIO.

INDUSTRIAL SUBAREAS
Height limit: 30 feet.
FAR: 0.5:1
"""


# ============================================================
# Context values used — provenance in output
# ============================================================

class TestContextValuesUsed:

    def test_subarea_recorded(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        sub_used = [v for v in result.context_values_used if v.field_name == "subarea"]
        assert len(sub_used) == 1
        assert sub_used[0].value == "Regional Commercial"
        assert sub_used[0].source == "parcel_profile_direct"
        assert sub_used[0].confidence == "high"

    def test_overlay_recorded(self):
        ctx = _make_context(overlay_name="San Pedro")
        result = find_relevant_sections("Some text about San Pedro.", ctx, "test")

        overlay_used = [v for v in result.context_values_used if v.field_name == "overlay_name"]
        assert len(overlay_used) == 1
        assert overlay_used[0].value == "San Pedro"

    def test_conflict_flag_on_context_value(self):
        ctx = _make_context(
            subarea="Regional Commercial",
            subarea_alts=[("Central Commercial E", ContextFieldSource.LINKER_INFERENCE)],
        )
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        sub_used = [v for v in result.context_values_used if v.field_name == "subarea"]
        assert sub_used[0].has_conflict is True
        assert "Central Commercial E" in sub_used[0].conflicting_values


# ============================================================
# Conflict weakening
# ============================================================

class TestConflictWeakening:

    def test_conflicted_subarea_weakens(self):
        ctx = _make_context(
            subarea="Regional Commercial",
            subarea_alts=[("Central Commercial E", ContextFieldSource.LINKER_INFERENCE)],
        )
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        assert result.narrowing_weakened_by_conflict is True
        assert any("conflicting" in w.lower() for w in result.warnings)

    def test_no_conflict_no_weakening(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        assert result.narrowing_weakened_by_conflict is False

    def test_weakening_warning_names_alternative(self):
        ctx = _make_context(
            subarea="Regional Commercial",
            subarea_alts=[("Central Commercial E", ContextFieldSource.LINKER_INFERENCE)],
        )
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        conflict_warnings = [w for w in result.warnings if "Central Commercial E" in w]
        assert len(conflict_warnings) >= 1


# ============================================================
# Inclusion/exclusion reasons
# ============================================================

class TestInclusionExclusionReasons:

    def test_exact_match_has_inclusion_reason(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        exact = [s for s in result.relevant_sections if s.match_strength == MatchStrength.EXACT]
        assert len(exact) >= 1
        assert exact[0].inclusion_reason is not None
        assert "Regional Commercial" in exact[0].inclusion_reason

    def test_excluded_has_exclusion_reason(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        excluded = result.excluded_sections
        assert len(excluded) >= 1
        for s in excluded:
            assert s.exclusion_reason is not None
            assert "different subarea" in s.exclusion_reason.lower()

    def test_exclusion_reason_names_wrong_subarea(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        excluded = result.excluded_sections
        exclusion_text = " ".join(s.exclusion_reason or "" for s in excluded).lower()
        # Should mention what the wrong subarea is
        assert "central commercial" in exclusion_text or "industrial" in exclusion_text

    def test_char_count_on_sections(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        for s in result.relevant_sections + result.excluded_sections:
            assert s.char_count > 0
            assert s.char_count == s.end_offset - s.start_offset

    def test_relevant_char_count_property(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        assert result.relevant_char_count > 0
        assert result.relevant_char_count < result.total_chars


# ============================================================
# Page-aware targeting
# ============================================================

class TestPageAwareTargeting:

    def test_page_numbers_populated(self):
        page1 = "CENTRAL COMMERCIAL SUBAREAS\nHeight: 45ft."
        page2 = "REGIONAL COMMERCIAL SUBAREA\nFAR: 4.0:1"
        page3 = "INDUSTRIAL SUBAREAS\nHeight: 30ft."
        full = page1 + "\n\n" + page2 + "\n\n" + page3

        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(full, ctx, "test", page_texts=[page1, page2, page3])

        assert result.total_pages == 3

        exact = [s for s in result.relevant_sections if s.match_strength == MatchStrength.EXACT]
        assert len(exact) >= 1
        assert exact[0].page_number == 2  # Regional Commercial is on page 2

    def test_excluded_have_page_numbers(self):
        page1 = "CENTRAL COMMERCIAL SUBAREAS\nOther content."
        page2 = "REGIONAL COMMERCIAL SUBAREA\nTarget content."
        full = page1 + "\n\n" + page2

        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(full, ctx, "test", page_texts=[page1, page2])

        for s in result.excluded_sections:
            assert s.page_number is not None

    def test_no_page_texts_means_no_page_numbers(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        for s in result.relevant_sections:
            assert s.page_number is None

    def test_real_zi2478_page_targeting(self):
        """Real ZI-2478: 3 pages. Page numbers should be populated."""
        import pdfplumber

        page_texts = []
        with pdfplumber.open(str(_ZI_2478_PDF)) as pdf:
            for page in pdf.pages:
                page_texts.append(page.extract_text() or "")
        full_text = "\n\n".join(page_texts)

        ctx = _make_context(overlay_name="San Pedro")
        result = find_relevant_sections(full_text, ctx, "ZI2478", page_texts=page_texts)

        assert result.total_pages == 3
        # At least some sections should have page numbers
        all_sections = result.relevant_sections + result.excluded_sections
        if all_sections:
            pages_assigned = [s.page_number for s in all_sections if s.page_number is not None]
            assert len(pages_assigned) > 0


# ============================================================
# Ambiguity
# ============================================================

class TestAmbiguity:

    def test_missing_subarea_is_ambiguous(self):
        ctx = _make_context(subarea="Nonexistent Subarea")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        assert result.is_ambiguous
        assert any("not found" in w.lower() for w in result.warnings)

    def test_found_subarea_is_not_ambiguous(self):
        ctx = _make_context(subarea="Regional Commercial")
        result = find_relevant_sections(_MULTI_SUBAREA_DOC, ctx, "test")

        assert not result.is_ambiguous
