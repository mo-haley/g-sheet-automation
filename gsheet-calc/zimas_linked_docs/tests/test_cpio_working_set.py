"""Tests for CPIO branch-bounded working-set extraction (Pass J).

Verifies that:
1. Clean subarea selection → BranchWorkingSet populated; primary/general/excluded split correct
2. Conflict-weakened selection → confidence="moderate", conflict_weakened=True
3. No subarea → confidence="uncertain", all entries in general, primary/excluded empty
4. Subarea provided but no match → confidence="weak", full doc surfaced as general
5. Identity-contested → confidence="weak", all entries as general, conflict_weakened=True
6. Unknown CPIO → branch_working_set is None (extraction not found)
7. Graceful absence of page-span data → span_coverage="none", entries still populated
8. working_set_summary is populated and reflects the effective working set
9. BranchEntry fields (label, span_known) are correctly translated from BranchEntryData
10. End-to-end: pipeline populates branch_working_set on CPIO record

San Pedro CPIO structure (for reference):
  Chapter I:   General Provisions         (general — no subarea family)
  Chapter II:  Regional Commercial Subarea (family: "Regional Commercial")
  Chapter III: Central Commercial Subareas (family: "Central Commercial")
  Chapter IV:  Coastal Commercial Subareas (family: "Coastal Commercial")
  Chapter V:   Multi-Family Residential Subarea (family: "Multi-Family Residential")
  Chapter VI:  Industrial Subareas        (family: "Industrial")
  Figure I:    CPIO District Overview Map  (general — no subarea family)
  Figure II:   Regional Commercial Subarea Map (family: "Regional Commercial")
  Figure III:  Central Commercial Subareas Map (family: "Central Commercial")
  Figure IV:   Coastal Commercial Subareas Map (family: "Coastal Commercial")
  Figure V:    Multi-Family Residential Subarea Map (family: "Multi-Family Residential")
  Figure VI:   Industrial Subareas Map     (family: "Industrial")

With cpio_subarea="Central Commercial-C":
  primary   = [Chapter III, Figure III]      (2 entries)
  general   = [Chapter I, Figure I]          (2 entries)
  excluded  = [II, IV, V, VI chapters + figs] (8 entries)
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    BranchEntry,
    BranchWorkingSet,
    DOC_TYPE_OVERLAY_CPIO,
    FETCH_NOW,
    FETCH_DEFER,
    CONF_DETECTED_NOT_INTERPRETED,
    LinkedDocRecord,
)
from zimas_linked_docs.cpio_fetch import run_cpio_extraction
from zimas_linked_docs.narrowing_context import (
    NarrowingContext, _set_field,
    SOURCE_CALLER_EXPLICIT, SOURCE_ZIMAS_PROFILE_FIELD,
)
from zimas_linked_docs.structure_extractor import extract_surface_fields, _build_branch_working_set
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline
from zimas_linked_docs.models import ZimasLinkedDocInput


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_cpio_record(doc_label: str = "San Pedro CPIO") -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id=f"test-cpio-{doc_label.replace(' ', '-').lower()}",
        doc_type=DOC_TYPE_OVERLAY_CPIO,
        doc_label=doc_label,
        usability_posture="manual_review_first",
        fetch_decision=FETCH_DEFER,  # CPIO extraction runs regardless of fetch_decision
        confidence_state=CONF_DETECTED_NOT_INTERPRETED,
    )


def _extract(doc_label: str, cpio_subarea: str | None = None) -> LinkedDocRecord:
    """Run extraction and return the mutated record."""
    record = _make_cpio_record(doc_label)
    ctx = None
    if cpio_subarea is not None:
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", cpio_subarea, SOURCE_CALLER_EXPLICIT, "high")
    extract_surface_fields([record], _narrowing_context=ctx)
    return record


def _extract_with_context(doc_label: str, ctx: NarrowingContext) -> LinkedDocRecord:
    record = _make_cpio_record(doc_label)
    extract_surface_fields([record], _narrowing_context=ctx)
    return record


# ── Section 1: Clean subarea selection ────────────────────────────────────────

class TestCleanSubareaSelection:
    def test_branch_working_set_is_populated(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert r.branch_working_set is not None

    def test_branch_working_set_is_branch_working_set_type(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert isinstance(r.branch_working_set, BranchWorkingSet)

    def test_selection_confidence_is_strong(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert r.branch_working_set.selection_confidence == "strong"

    def test_conflict_weakened_is_false(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert r.branch_working_set.conflict_weakened is False

    def test_primary_entries_count(self):
        """Chapter III + Figure III = 2 primary entries."""
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert len(r.branch_working_set.primary) == 2

    def test_primary_entries_are_branch_entry_type(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        for e in r.branch_working_set.primary:
            assert isinstance(e, BranchEntry)

    def test_primary_entry_labels_contain_central_commercial(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        labels = [e.label for e in r.branch_working_set.primary]
        assert any("Central Commercial" in lbl for lbl in labels)

    def test_general_entries_count(self):
        """Chapter I + Figure I = 2 general entries (no subarea family)."""
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert len(r.branch_working_set.general) == 2

    def test_general_entries_include_chapter_one(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        labels = [e.label for e in r.branch_working_set.general]
        assert any("Chapter I" in lbl for lbl in labels)

    def test_excluded_entries_count(self):
        """All other subarea chapters+figures: II, IV, V, VI × 2 each = 8 excluded."""
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert len(r.branch_working_set.excluded) == 8

    def test_excluded_entries_do_not_contain_central_commercial(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        labels = [e.label for e in r.branch_working_set.excluded]
        assert not any("Central Commercial" in lbl for lbl in labels)

    def test_different_subarea_gives_different_primary(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Regional Commercial")
        labels = [e.label for e in r.branch_working_set.primary]
        assert any("Regional Commercial" in lbl for lbl in labels)

    def test_working_set_is_primary_plus_general(self):
        """Effective working set = primary + general (not excluded)."""
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        total = len(r.branch_working_set.primary) + len(r.branch_working_set.general)
        excluded = len(r.branch_working_set.excluded)
        assert total + excluded == 12  # 6 chapters + 6 figures


# ── Section 2: Conflict-weakened selection ────────────────────────────────────

class TestConflictWeakenedSelection:
    def _make_conflict_ctx(self, subarea: str) -> NarrowingContext:
        """Create a context with a real subarea conflict (two competing values)."""
        ctx = NarrowingContext()
        # Two different sources provide different subareas → conflict recorded
        _set_field(ctx, "subarea", subarea, SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        return ctx

    def test_conflict_weakened_confidence_is_moderate(self):
        ctx = self._make_conflict_ctx("Central Commercial-C")
        r = _extract_with_context("San Pedro CPIO", ctx)
        assert r.branch_working_set.selection_confidence == "moderate"

    def test_conflict_weakened_flag_is_true(self):
        ctx = self._make_conflict_ctx("Central Commercial-C")
        r = _extract_with_context("San Pedro CPIO", ctx)
        assert r.branch_working_set.conflict_weakened is True

    def test_conflict_weakened_still_has_primary_entries(self):
        """Match is still performed despite conflict — only confidence is capped."""
        ctx = self._make_conflict_ctx("Central Commercial-C")
        r = _extract_with_context("San Pedro CPIO", ctx)
        assert len(r.branch_working_set.primary) == 2

    def test_conflict_weakened_summary_mentions_conflict(self):
        ctx = self._make_conflict_ctx("Central Commercial-C")
        r = _extract_with_context("San Pedro CPIO", ctx)
        assert "conflict" in r.branch_working_set.working_set_summary.lower()


# ── Section 3: No subarea → uncertain ────────────────────────────────────────

class TestNoSubareaSelection:
    def test_selection_confidence_is_uncertain(self):
        r = _extract("San Pedro CPIO")
        assert r.branch_working_set.selection_confidence == "uncertain"

    def test_primary_entries_empty(self):
        r = _extract("San Pedro CPIO")
        assert r.branch_working_set.primary == []

    def test_excluded_entries_empty(self):
        r = _extract("San Pedro CPIO")
        assert r.branch_working_set.excluded == []

    def test_general_entries_contains_all_12_items(self):
        """All 12 items (6 chapters + 6 figures) surface as general."""
        r = _extract("San Pedro CPIO")
        assert len(r.branch_working_set.general) == 12

    def test_working_set_summary_mentions_subarea_needed(self):
        r = _extract("San Pedro CPIO")
        summary = r.branch_working_set.working_set_summary.lower()
        assert "subarea" in summary


# ── Section 4: Subarea provided but no match → weak ──────────────────────────

class TestSubareaNonMatchSelection:
    def test_selection_confidence_is_weak(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Nonexistent District X")
        assert r.branch_working_set.selection_confidence == "weak"

    def test_all_items_surface_as_general(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Nonexistent District X")
        assert len(r.branch_working_set.general) == 12

    def test_primary_and_excluded_empty(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Nonexistent District X")
        assert r.branch_working_set.primary == []
        assert r.branch_working_set.excluded == []


# ── Section 5: Identity-contested → weak + conflict_weakened ─────────────────

class TestIdentityContestedSelection:
    def _make_contested_ctx(self, overlay_name: str) -> NarrowingContext:
        """Build a context where overlay_name resolves to a different CPIO."""
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", overlay_name, SOURCE_CALLER_EXPLICIT, "high")
        return ctx

    def test_identity_contested_sets_confidence_weak(self):
        # doc_label = "San Pedro CPIO", but context says "Venice CPIO"
        # Venice resolves to None (not in registry), so this won't be contested.
        # Use a hypothetical second registered CPIO. For now, test via run_cpio_extraction directly.
        result = run_cpio_extraction("San Pedro CPIO", identity_contested=True)
        ws = _build_branch_working_set(result)
        assert ws.selection_confidence == "weak"

    def test_identity_contested_sets_conflict_weakened(self):
        result = run_cpio_extraction("San Pedro CPIO", identity_contested=True)
        ws = _build_branch_working_set(result)
        assert ws.conflict_weakened is True

    def test_identity_contested_all_entries_as_general(self):
        result = run_cpio_extraction("San Pedro CPIO", identity_contested=True)
        ws = _build_branch_working_set(result)
        assert len(ws.general) == 12
        assert ws.primary == []
        assert ws.excluded == []


# ── Section 6: Unknown CPIO → branch_working_set is None ──────────────────────

class TestUnknownCPIOWorkingSet:
    def test_unknown_cpio_branch_working_set_is_none(self):
        r = _extract("Venice CPIO")  # not in known-structure registry
        assert r.branch_working_set is None

    def test_unknown_cpio_extraction_status(self):
        result = run_cpio_extraction("Venice CPIO")
        assert result.extraction_status == "not_found"


# ── Section 7: Page-span data absent → span_coverage="none" ──────────────────

class TestPageSpanAbsent:
    def test_span_coverage_is_none_when_no_spans_recorded(self):
        """San Pedro has no page spans recorded yet."""
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert r.branch_working_set.span_coverage == "none"

    def test_entries_still_populated_without_span_data(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        ws = r.branch_working_set
        assert len(ws.primary) + len(ws.general) > 0

    def test_entry_span_known_is_false_when_no_spans(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        for e in r.branch_working_set.primary + r.branch_working_set.general:
            assert e.span_known is False

    def test_entry_page_start_is_none_when_no_spans(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        for e in r.branch_working_set.primary + r.branch_working_set.general:
            assert e.page_start is None


# ── Section 8: working_set_summary content ────────────────────────────────────

class TestWorkingSetSummary:
    def test_summary_populated_for_strong_selection(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert r.branch_working_set.working_set_summary != ""

    def test_summary_mentions_primary_branches(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert "primary" in r.branch_working_set.working_set_summary.lower()

    def test_summary_mentions_excluded_count(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert "excluded" in r.branch_working_set.working_set_summary.lower()

    def test_summary_mentions_page_spans_not_available(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert "page span" in r.branch_working_set.working_set_summary.lower()

    def test_uncertain_summary_mentions_subarea(self):
        r = _extract("San Pedro CPIO")
        assert "subarea" in r.branch_working_set.working_set_summary.lower()


# ── Section 9: BranchEntry field translation ──────────────────────────────────

class TestBranchEntryFields:
    def test_entry_label_is_string(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        for e in r.branch_working_set.primary:
            assert isinstance(e.label, str)
            assert len(e.label) > 0

    def test_entry_label_format_chapter(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        chapter_entries = [e for e in r.branch_working_set.primary if "Chapter" in e.label]
        assert chapter_entries
        for e in chapter_entries:
            assert ": " in e.label

    def test_entry_label_format_figure(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        figure_entries = [e for e in r.branch_working_set.primary if "Figure" in e.label]
        assert figure_entries
        for e in figure_entries:
            assert ": " in e.label

    def test_excluded_entry_labels_are_distinct_from_primary(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        primary_labels = {e.label for e in r.branch_working_set.primary}
        excluded_labels = {e.label for e in r.branch_working_set.excluded}
        assert primary_labels.isdisjoint(excluded_labels)

    def test_general_entry_labels_are_distinct_from_primary(self):
        r = _extract("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        primary_labels = {e.label for e in r.branch_working_set.primary}
        general_labels = {e.label for e in r.branch_working_set.general}
        assert primary_labels.isdisjoint(general_labels)


# ── Section 10: End-to-end pipeline ───────────────────────────────────────────

class TestPipelineWorkingSet:
    def test_pipeline_populates_branch_working_set_for_cpio(self):
        inp = ZimasLinkedDocInput(
            apn="1234-567-890",
            overlay_zones=["San Pedro CPIO"],
            cpio_subarea="Central Commercial-C",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records
        assert cpio_records[0].branch_working_set is not None

    def test_pipeline_working_set_confidence_strong_with_subarea(self):
        inp = ZimasLinkedDocInput(
            apn="1234-567-890",
            overlay_zones=["San Pedro CPIO"],
            cpio_subarea="Central Commercial-C",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records[0].branch_working_set.selection_confidence == "strong"

    def test_pipeline_working_set_none_for_unknown_cpio(self):
        inp = ZimasLinkedDocInput(
            apn="1234-567-890",
            overlay_zones=["Venice CPIO"],
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records
        assert cpio_records[0].branch_working_set is None

    def test_pipeline_working_set_uncertain_without_subarea(self):
        inp = ZimasLinkedDocInput(
            apn="1234-567-890",
            overlay_zones=["San Pedro CPIO"],
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records[0].branch_working_set.selection_confidence == "uncertain"

    def test_pipeline_cpio_fetch_decision_irrelevant_for_working_set(self):
        """CPIO extraction runs even when fetch_decision=FETCH_DEFER."""
        inp = ZimasLinkedDocInput(
            apn="1234-567-890",
            overlay_zones=["San Pedro CPIO"],
            cpio_subarea="Industrial-A",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        # Extraction should have run regardless — working_set must be present
        assert cpio_records[0].branch_working_set is not None
        assert cpio_records[0].branch_working_set.selection_confidence in ("strong", "moderate")
