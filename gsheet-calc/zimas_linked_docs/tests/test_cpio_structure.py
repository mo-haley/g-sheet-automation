"""Tests for CPIO known-structure extraction and branch selection.

Scenarios:
1. San Pedro CPIO with subarea → strong branch selection, primary branches found
2. San Pedro CPIO without subarea → uncertain, all chapters treated as general
3. Unknown CPIO → not_found, record stays at detected_not_interpreted
4. CPIO with known structure → confidence promoted to surface_usable by confidence.py
5. Branch excluded/general labels populated correctly
6. Alias ("Coastal San Pedro CPIO") resolves to San Pedro known structure
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_OVERLAY_CPIO,
    FETCH_NOW,
    CONF_SURFACE_USABLE,
    CONF_DETECTED_NOT_INTERPRETED,
    LinkedDocRecord,
)
from zimas_linked_docs.cpio_fetch import (
    CPIOExtractionResult,
    run_cpio_extraction,
    _normalize_cpio_name,
    _resolve_cpio_name,
)
from zimas_linked_docs.narrowing_context import NarrowingContext, _set_field, SOURCE_CALLER_EXPLICIT
from zimas_linked_docs.structure_extractor import extract_surface_fields
from zimas_linked_docs.confidence import assign_confidence_states


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cpio_record(
    doc_label: str = "San Pedro CPIO",
    fetch_decision: str = FETCH_NOW,
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id=f"test-cpio-{doc_label.replace(' ', '-').lower()}",
        doc_type=DOC_TYPE_OVERLAY_CPIO,
        doc_label=doc_label,
        usability_posture="manual_review_first",
        fetch_decision=fetch_decision,
        confidence_state=confidence_state,
        doc_type_notes="CPIO detected by name.",
    )


# ── Scenario 1: San Pedro with subarea → strong branch selection ──────────────

class TestSanPedroCPIOWithSubarea:
    def test_extraction_status_success(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert result.extraction_status == "success"

    def test_document_identifier_populated(self):
        result = run_cpio_extraction("San Pedro CPIO")
        assert result.document_identifier == "San Pedro CPIO (Ord. 185539)"

    def test_ordinance_number_populated(self):
        result = run_cpio_extraction("San Pedro CPIO")
        assert result.ordinance_number == "185539"

    def test_all_chapters_in_chapter_labels(self):
        result = run_cpio_extraction("San Pedro CPIO")
        assert len(result.chapter_labels) == 6
        assert any("General Provisions" in lbl for lbl in result.chapter_labels)
        assert any("Central Commercial" in lbl for lbl in result.chapter_labels)

    def test_all_figures_in_figure_labels(self):
        result = run_cpio_extraction("San Pedro CPIO")
        assert len(result.figure_labels) == 6

    def test_primary_branch_found_for_central_commercial(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert result.branch_selection_confidence == "strong"
        assert any("Central Commercial" in lbl for lbl in result.branch_primary_labels)

    def test_general_provisions_in_general_branches(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        assert any("General Provisions" in lbl for lbl in result.branch_general_labels)
        # Chapter I has no subarea family → general
        assert any("Chapter I" in lbl for lbl in result.branch_general_labels)

    def test_other_subareas_in_excluded(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Central Commercial-C")
        # Regional Commercial, Coastal Commercial, Multi-Family, Industrial should be excluded
        excluded_families = [lbl for lbl in result.branch_excluded_labels]
        assert any("Regional Commercial" in lbl for lbl in excluded_families)
        assert any("Industrial" in lbl for lbl in excluded_families)

    def test_primary_branch_for_industrial_subarea(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Industrial-A")
        assert result.branch_selection_confidence == "strong"
        assert any("Industrial" in lbl for lbl in result.branch_primary_labels)
        # Central Commercial should be excluded
        assert any("Central Commercial" in lbl for lbl in result.branch_excluded_labels)


# ── Scenario 2: San Pedro without subarea → uncertain ────────────────────────

class TestSanPedroCPIOWithoutSubarea:
    def test_extraction_still_succeeds(self):
        result = run_cpio_extraction("San Pedro CPIO")
        assert result.extraction_status == "success"

    def test_branch_confidence_uncertain(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea=None)
        assert result.branch_selection_confidence == "uncertain"

    def test_no_primary_branches(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea=None)
        assert result.branch_primary_labels == []

    def test_all_chapters_in_general_when_no_subarea(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea=None)
        # All chapters surface as general when no subarea
        assert len(result.branch_general_labels) == len(result.chapter_labels) + len(result.figure_labels)

    def test_notes_prompt_for_subarea(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea=None)
        assert "subarea" in result.branch_selection_notes.lower()


# ── Scenario 3: Unknown CPIO → not_found ─────────────────────────────────────

class TestUnknownCPIO:
    def test_extraction_status_not_found(self):
        result = run_cpio_extraction("Venice CPIO")
        assert result.extraction_status == "not_found"

    def test_no_chapters_for_unknown(self):
        result = run_cpio_extraction("Venice CPIO")
        assert result.chapter_labels == []
        assert result.figure_labels == []

    def test_issues_populated_for_unknown(self):
        result = run_cpio_extraction("Venice CPIO")
        assert result.issues  # at least one issue

    def test_record_stays_at_detected_not_interpreted_for_unknown(self):
        record = _make_cpio_record(doc_label="Venice CPIO")
        extract_surface_fields([record])  # _fetch_enabled=False doesn't matter for CPIO

        # Extraction was not possible; confidence should stay as-is
        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_record_fetch_status_skipped_for_unknown(self):
        record = _make_cpio_record(doc_label="Venice CPIO")
        extract_surface_fields([record])
        assert record.fetch_status == "skipped"


# ── Scenario 4: Known CPIO → confidence promoted to surface_usable ───────────

class TestKnownCPIOConfidencePromotion:
    def test_surface_usable_after_known_structure_extraction(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record])

        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_SURFACE_USABLE

    def test_fetch_status_success_after_known_structure(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record])
        assert record.fetch_status == "success"

    def test_chapters_populated_on_record(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record])
        assert len(record.extracted_chapter_list) == 6

    def test_figures_populated_on_record(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record])
        assert len(record.extracted_figure_labels) == 6

    def test_ordinance_number_on_record(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record])
        assert record.extracted_ordinance_number == "185539"

    def test_branch_fields_populated_on_record(self):
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Regional Commercial", SOURCE_CALLER_EXPLICIT, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_selection_confidence == "strong"
        assert any("Regional Commercial" in lbl for lbl in record.branch_primary_labels)

    def test_cpio_runs_when_fetch_disabled(self):
        """CPIO known-structure extraction is not gated by _fetch_enabled."""
        record = _make_cpio_record(doc_label="San Pedro CPIO")
        extract_surface_fields([record], _fetch_enabled=False)
        # Known-structure extraction should still run
        assert record.fetch_status == "success"
        assert record.extracted_chapter_list != []


# ── Scenario 5: Branch excluded/general labels ───────────────────────────────

class TestBranchLabelPopulation:
    def test_weak_confidence_when_subarea_unrecognised(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Nonexistent District")
        assert result.branch_selection_confidence == "weak"

    def test_no_excluded_on_weak_match(self):
        """On weak match all chapters surface as general (no excluded)."""
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Nonexistent District")
        assert result.branch_excluded_labels == []

    def test_all_chapters_general_on_weak_match(self):
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Nonexistent District")
        # All chapters should appear in general_labels
        assert len(result.branch_general_labels) > 0

    def test_figure_i_in_general_no_subarea(self):
        """Figure I has no subarea family → always general."""
        result = run_cpio_extraction("San Pedro CPIO", cpio_subarea="Industrial-A")
        assert any("Figure I:" in lbl for lbl in result.branch_general_labels)


# ── Scenario 6: Alias resolution ─────────────────────────────────────────────

class TestAliasResolution:
    def test_coastal_san_pedro_resolves_to_san_pedro(self):
        result = run_cpio_extraction("Coastal San Pedro CPIO")
        assert result.extraction_status == "success"
        assert result.resolved_name == "san pedro"

    def test_alias_has_same_structure_as_canonical(self):
        canonical = run_cpio_extraction("San Pedro CPIO")
        alias = run_cpio_extraction("Coastal San Pedro CPIO")
        assert alias.chapter_labels == canonical.chapter_labels
        assert alias.ordinance_number == canonical.ordinance_number

    def test_normalize_strips_cpio_suffix(self):
        assert _normalize_cpio_name("San Pedro CPIO") == "san pedro"
        assert _normalize_cpio_name("san pedro cpio") == "san pedro"
        assert _normalize_cpio_name("San Pedro") == "san pedro"

    def test_resolve_returns_none_for_unknown(self):
        key, structure = _resolve_cpio_name("Venice CPIO")
        assert key is None
        assert structure is None
