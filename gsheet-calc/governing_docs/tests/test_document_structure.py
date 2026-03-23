"""Tests for document structure parsing and branch selection.

Tests against the real San Pedro CPIO ordinance structure
(Chapter I = General, II = Regional Commercial, III = Central Commercial
A-E, IV = Coastal Commercial A-B, V = Multi-Family Residential,
VI = Industrial A-D).
"""

from __future__ import annotations

import pytest

from governing_docs.document_context import (
    ContextField,
    ContextFieldAlternative,
    ContextFieldConfidence,
    ContextFieldSource,
    DocumentContext,
)
from governing_docs.document_structure import (
    BranchRelevance,
    DocumentChapter,
    DocumentStructure,
    get_known_cpio_structure,
    select_branches,
)


@pytest.fixture()
def san_pedro_structure():
    return get_known_cpio_structure("San Pedro")


def _ctx_with_subarea(
    subarea: str,
    source: ContextFieldSource = ContextFieldSource.PARCEL_PROFILE_DIRECT,
    alts: list[tuple[str, ContextFieldSource]] | None = None,
) -> DocumentContext:
    ctx = DocumentContext()
    alternatives = []
    if alts:
        for val, src in alts:
            alternatives.append(ContextFieldAlternative(
                value=val, source=src,
                confidence=ContextFieldConfidence.MEDIUM,
                reason_not_primary=f"Lower priority than {source.value}",
            ))
    ctx.subarea = ContextField(
        value=subarea, source=source,
        confidence=ContextFieldConfidence.HIGH,
        alternatives=alternatives,
    )
    ctx.overlay_name = ContextField(
        value="San Pedro", source=ContextFieldSource.PARCEL_PROFILE_DIRECT,
        confidence=ContextFieldConfidence.HIGH,
    )
    return ctx


# ============================================================
# Known structure lookup
# ============================================================

class TestKnownStructure:

    def test_san_pedro_exists(self):
        s = get_known_cpio_structure("San Pedro")
        assert s is not None

    def test_san_pedro_cpio_suffix(self):
        s = get_known_cpio_structure("San Pedro CPIO")
        assert s is not None

    def test_san_pedro_case_insensitive(self):
        s = get_known_cpio_structure("san pedro")
        assert s is not None

    def test_san_pedro_has_6_chapters(self, san_pedro_structure):
        assert san_pedro_structure.chapter_count == 6

    def test_san_pedro_has_6_figures(self, san_pedro_structure):
        assert len(san_pedro_structure.figures) == 6

    def test_chapter_i_is_general(self, san_pedro_structure):
        ch1 = san_pedro_structure.chapters[0]
        assert ch1.number == "I"
        assert ch1.subarea_family is None  # General provisions

    def test_chapter_ii_is_regional_commercial(self, san_pedro_structure):
        ch2 = san_pedro_structure.chapters[1]
        assert ch2.number == "II"
        assert ch2.subarea_family == "Regional Commercial"

    def test_chapter_iii_has_subareas_a_through_e(self, san_pedro_structure):
        ch3 = san_pedro_structure.chapters[2]
        assert ch3.number == "III"
        assert ch3.subarea_family == "Central Commercial"
        assert ch3.subareas == ["A", "B", "C", "D", "E"]

    def test_unknown_cpio_returns_none(self):
        assert get_known_cpio_structure("Nonexistent") is None

    def test_get_chapter_by_subarea(self, san_pedro_structure):
        ch = san_pedro_structure.get_chapter_by_subarea("Regional Commercial")
        assert ch is not None
        assert ch.number == "II"

    def test_get_chapter_by_subarea_case_insensitive(self, san_pedro_structure):
        ch = san_pedro_structure.get_chapter_by_subarea("regional commercial")
        assert ch is not None


# ============================================================
# Branch selection — Regional Commercial subarea
# ============================================================

class TestBranchSelectionRegionalCommercial:

    def test_chapter_ii_is_primary(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        primary_chapters = [b for b in sel.primary_branches if b.branch_type == "chapter"]
        assert len(primary_chapters) == 1
        assert primary_chapters[0].number == "II"
        assert primary_chapters[0].relevance == BranchRelevance.PRIMARY

    def test_chapter_i_is_general(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        general = [b for b in sel.general_branches if b.branch_type == "chapter"]
        assert len(general) == 1
        assert general[0].number == "I"

    def test_other_chapters_excluded(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        excluded_nums = {b.number for b in sel.excluded_branches if b.branch_type == "chapter"}
        assert "III" in excluded_nums  # Central Commercial
        assert "IV" in excluded_nums   # Coastal Commercial
        assert "V" in excluded_nums    # Multi-Family Residential
        assert "VI" in excluded_nums   # Industrial

    def test_figure_ii_is_primary(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        primary_figs = [b for b in sel.primary_branches if b.branch_type == "figure"]
        assert any(f.number == "II" for f in primary_figs)

    def test_primary_reason_mentions_subarea(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        primary = sel.primary_branches[0]
        assert "Regional Commercial" in primary.reason

    def test_excluded_reason_mentions_wrong_subarea(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        excluded = sel.excluded_branches[0]
        assert "does not match" in excluded.reason


# ============================================================
# Branch selection — Central Commercial E subarea
# ============================================================

class TestBranchSelectionCentralCommercialE:

    def test_chapter_iii_is_primary(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Central Commercial E")
        sel = select_branches(san_pedro_structure, ctx)

        primary_chapters = [b for b in sel.primary_branches if b.branch_type == "chapter"]
        assert len(primary_chapters) == 1
        assert primary_chapters[0].number == "III"

    def test_chapter_ii_is_excluded(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Central Commercial E")
        sel = select_branches(san_pedro_structure, ctx)

        excluded_nums = {b.number for b in sel.excluded_branches if b.branch_type == "chapter"}
        assert "II" in excluded_nums


# ============================================================
# Conflict weakening
# ============================================================

class TestBranchConflictWeakening:

    def test_conflicted_subarea_weakens(self, san_pedro_structure):
        ctx = _ctx_with_subarea(
            "Regional Commercial",
            alts=[("Central Commercial E", ContextFieldSource.LINKER_INFERENCE)],
        )
        sel = select_branches(san_pedro_structure, ctx)

        assert sel.conflict_weakened is True
        assert any("conflicting" in w.lower() for w in sel.warnings)

    def test_conflict_warning_names_alternative(self, san_pedro_structure):
        ctx = _ctx_with_subarea(
            "Regional Commercial",
            alts=[("Central Commercial E", ContextFieldSource.LINKER_INFERENCE)],
        )
        sel = select_branches(san_pedro_structure, ctx)

        assert any("Central Commercial E" in w for w in sel.warnings)

    def test_no_conflict_no_weakening(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        assert sel.conflict_weakened is False


# ============================================================
# No subarea context
# ============================================================

class TestNoSubareaContext:

    def test_all_chapters_uncertain(self, san_pedro_structure):
        ctx = DocumentContext()  # No subarea
        sel = select_branches(san_pedro_structure, ctx)

        assert len(sel.uncertain_branches) == san_pedro_structure.chapter_count
        assert len(sel.primary_branches) == 0

    def test_warning_generated(self, san_pedro_structure):
        ctx = DocumentContext()
        sel = select_branches(san_pedro_structure, ctx)

        assert any("no subarea" in w.lower() for w in sel.warnings)


# ============================================================
# Missing subarea in structure
# ============================================================

class TestMissingSubareaInStructure:

    def test_nonexistent_subarea_no_primary(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Waterfront Special District")
        sel = select_branches(san_pedro_structure, ctx)

        assert not sel.has_primary
        assert any("no chapter" in w.lower() for w in sel.warnings)

    def test_nonexistent_subarea_is_ambiguous(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Waterfront Special District")
        sel = select_branches(san_pedro_structure, ctx)

        # Not ambiguous because we have a subarea — it just doesn't match any chapter
        # The system correctly warns but doesn't set uncertain (subarea IS known)
        assert not sel.is_ambiguous  # uncertain_branches is empty; we have subarea context


# ============================================================
# Audit fields
# ============================================================

class TestAuditFields:

    def test_target_subarea_recorded(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        assert sel.target_subarea == "Regional Commercial"
        assert sel.target_subarea_source == "parcel_profile_direct"

    def test_target_overlay_recorded(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        assert sel.target_overlay == "San Pedro"

    def test_document_identifier(self, san_pedro_structure):
        ctx = _ctx_with_subarea("Regional Commercial")
        sel = select_branches(san_pedro_structure, ctx)

        assert "San Pedro" in sel.document_identifier
