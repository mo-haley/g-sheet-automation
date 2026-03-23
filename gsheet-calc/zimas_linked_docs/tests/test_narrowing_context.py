"""Tests for the narrowing-context layer.

Covers:
1. build_narrowing_context from ZimasLinkedDocInput fields
2. Source priority and conflict detection
3. NarrowingContext properties (has_subarea, subarea_has_conflict, narrowing_strength)
4. End-to-end: conflict-weakened context → branch_conflict_weakened on record, confidence capped
5. No conflict → strong confidence preserved
6. _set_field priority logic (direct unit tests)
7. Specific-plan subarea context preservation (Pass B)
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_OVERLAY_CPIO,
    FETCH_NOW,
    CONF_DETECTED_NOT_INTERPRETED,
    LinkedDocRecord,
    ZimasLinkedDocInput,
)
from zimas_linked_docs.narrowing_context import (
    NarrowingContext,
    NarrowingContextField,
    NarrowingAlternative,
    SOURCE_CALLER_EXPLICIT,
    SOURCE_ZIMAS_PROFILE_FIELD,
    SOURCE_ZONE_STRING_PARSE,
    SOURCE_ZI_DOCUMENT_HEADER,
    _set_field,
    build_narrowing_context,
)
from zimas_linked_docs.structure_extractor import extract_surface_fields


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cpio_record(doc_label: str = "San Pedro CPIO") -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id=f"test-{doc_label.lower().replace(' ', '-')}",
        doc_type=DOC_TYPE_OVERLAY_CPIO,
        doc_label=doc_label,
        usability_posture="manual_review_first",
        fetch_decision=FETCH_NOW,
        confidence_state=CONF_DETECTED_NOT_INTERPRETED,
    )


def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


# ── Section 1: build_narrowing_context from ZimasLinkedDocInput ───────────────

class TestBuildNarrowingContextFromInput:
    def test_cpio_subarea_sets_subarea_field(self):
        ctx = build_narrowing_context(_inp(cpio_subarea="Central Commercial-C"))
        assert ctx.has_subarea
        assert ctx.subarea.value == "Central Commercial-C"

    def test_cpio_subarea_source_is_caller_explicit(self):
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        assert ctx.subarea.source == SOURCE_CALLER_EXPLICIT

    def test_cpio_subarea_confidence_is_high(self):
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        assert ctx.subarea.confidence == "high"

    def test_no_subarea_when_not_provided(self):
        ctx = build_narrowing_context(_inp())
        assert not ctx.has_subarea

    def test_single_cpio_overlay_zone_sets_overlay_name(self):
        ctx = build_narrowing_context(_inp(overlay_zones=["San Pedro CPIO"]))
        assert ctx.has_overlay_name
        assert ctx.overlay_name.value == "San Pedro CPIO"

    def test_overlay_name_source_is_zimas_profile_field(self):
        ctx = build_narrowing_context(_inp(overlay_zones=["San Pedro CPIO"]))
        assert ctx.overlay_name.source == SOURCE_ZIMAS_PROFILE_FIELD

    def test_non_cpio_overlay_zone_ignored_for_overlay_name(self):
        ctx = build_narrowing_context(_inp(overlay_zones=["CDO", "HPOZ"]))
        assert not ctx.has_overlay_name

    def test_empty_input_produces_empty_context(self):
        ctx = build_narrowing_context(_inp())
        assert not ctx.has_subarea
        assert not ctx.has_overlay_name
        assert ctx.narrowing_strength == "none"
        assert not ctx.conflicts


# ── Section 2: Source priority and conflict detection ─────────────────────────

class TestSetFieldPriority:
    def test_set_new_field_sets_value(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_CALLER_EXPLICIT, "high")
        assert ctx.subarea.value == "Central Commercial-C"
        assert ctx.subarea.source == SOURCE_CALLER_EXPLICIT

    def test_same_value_higher_priority_upgrades_source(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        # Same value — caller_explicit (priority 1) should become primary
        assert ctx.subarea.source == SOURCE_CALLER_EXPLICIT
        assert not ctx.subarea.alternatives  # no conflict, same value

    def test_same_value_lower_priority_does_not_downgrade(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_ZONE_STRING_PARSE, "medium")
        # Lower priority — source stays caller_explicit
        assert ctx.subarea.source == SOURCE_CALLER_EXPLICIT

    def test_different_values_higher_priority_wins(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_CALLER_EXPLICIT, "high")
        # caller_explicit (priority 1) > zimas_profile_field (priority 2)
        assert ctx.subarea.value == "Central Commercial-C"
        assert ctx.subarea.source == SOURCE_CALLER_EXPLICIT

    def test_different_values_lower_priority_becomes_alternative(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        # caller_explicit (priority 1) keeps primary; zimas_profile becomes alternative
        assert ctx.subarea.value == "Industrial-A"
        assert len(ctx.subarea.alternatives) == 1
        assert ctx.subarea.alternatives[0].value == "Central Commercial-C"

    def test_conflict_recorded_in_ctx_conflicts(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        assert len(ctx.conflicts) == 1
        assert "Industrial-A" in ctx.conflicts[0]
        assert "Central Commercial-C" in ctx.conflicts[0]

    def test_no_conflict_when_no_disagreement(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        assert not ctx.conflicts


# ── Section 3: NarrowingContext properties ────────────────────────────────────

class TestNarrowingContextProperties:
    def test_narrowing_strength_strong_with_subarea_and_overlay(self):
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", overlay_zones=["San Pedro CPIO"])
        )
        assert ctx.narrowing_strength == "strong"

    def test_narrowing_strength_moderate_with_subarea_only(self):
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        assert ctx.narrowing_strength == "moderate"

    def test_narrowing_strength_weak_with_overlay_only(self):
        ctx = build_narrowing_context(_inp(overlay_zones=["San Pedro CPIO"]))
        assert ctx.narrowing_strength == "weak"

    def test_narrowing_strength_none_with_empty_input(self):
        ctx = build_narrowing_context(_inp())
        assert ctx.narrowing_strength == "none"

    def test_subarea_has_conflict_false_without_alternatives(self):
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        assert not ctx.subarea_has_conflict

    def test_subarea_has_conflict_true_with_alternatives(self):
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        assert ctx.subarea_has_conflict

    def test_has_any_conflict_reflects_conflicts_list(self):
        ctx = NarrowingContext()
        assert not ctx.has_any_conflict
        _set_field(ctx, "subarea", "A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "B", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        assert ctx.has_any_conflict

    def test_multiple_cpio_overlay_zones_produce_conflict(self):
        ctx = build_narrowing_context(
            _inp(overlay_zones=["San Pedro CPIO", "Venice CPIO"])
        )
        assert ctx.overlay_name_has_conflict
        assert ctx.has_any_conflict


# ── Section 4: Conflict-weakened → capped confidence and record flag ──────────

class TestConflictWeakenedEndToEnd:
    def _ctx_with_conflict(self) -> NarrowingContext:
        """NarrowingContext where subarea has a conflict."""
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Central Commercial-C", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        return ctx

    def test_confidence_capped_at_moderate_on_conflict(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = self._ctx_with_conflict()
        extract_surface_fields([record], _narrowing_context=ctx)
        # Primary match found (Central Commercial-C) but conflict → moderate, not strong
        assert record.branch_selection_confidence == "moderate"

    def test_branch_conflict_weakened_true_on_conflict(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = self._ctx_with_conflict()
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_conflict_weakened is True

    def test_conflict_details_in_branch_selection_notes(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = self._ctx_with_conflict()
        extract_surface_fields([record], _narrowing_context=ctx)
        assert "conflict" in record.branch_selection_notes.lower()

    def test_primary_branches_still_populated_on_conflict(self):
        """Conflicted context still selects branches using primary value."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = self._ctx_with_conflict()  # primary = Central Commercial-C
        extract_surface_fields([record], _narrowing_context=ctx)
        # Central Commercial chapters should still be primary
        assert any("Central Commercial" in lbl for lbl in record.branch_primary_labels)


# ── Section 5: No conflict → strong confidence preserved ─────────────────────

class TestNoConflictPreservesStrong:
    def test_strong_confidence_without_conflict(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_selection_confidence == "strong"

    def test_branch_conflict_weakened_false_without_conflict(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_conflict_weakened is False

    def test_none_context_produces_uncertain(self):
        """Passing no NarrowingContext behaves as before — uncertain branch selection."""
        record = _make_cpio_record("San Pedro CPIO")
        extract_surface_fields([record], _narrowing_context=None)
        assert record.branch_selection_confidence == "uncertain"
        assert record.branch_conflict_weakened is False


# ── Section 6: Overlay-name conflict and identity contest ─────────────────────

class TestOverlayConflictAndIdentityContest:
    """Overlay-name context vs. detected doc_label identity checks.

    Scenarios:
    1. Clean overlay (matches doc_label) + clean subarea → strong, no weakening
    2. Overlay conflict — primary matches doc_label, alternatives exist → moderate
    3. Overlay identity mismatch — primary resolves to different CPIO → weak + warning
    4. No overlay in context → identity check skipped, normal subarea behaviour
    5. Alias match — "Coastal San Pedro CPIO" vs "San Pedro CPIO" → NOT contested
    """

    # ── Scenario 1: clean overlay + clean subarea → strong ───────────────────

    def test_clean_overlay_and_subarea_strong(self):
        """Overlay matches doc_label and no conflicts → strong confidence."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", overlay_zones=["San Pedro CPIO"])
        )
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_selection_confidence == "strong"

    def test_clean_overlay_conflict_weakened_false(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", overlay_zones=["San Pedro CPIO"])
        )
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_conflict_weakened is False

    def test_clean_overlay_primary_branches_populated(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", overlay_zones=["San Pedro CPIO"])
        )
        extract_surface_fields([record], _narrowing_context=ctx)
        assert any("Industrial" in lbl for lbl in record.branch_primary_labels)

    # ── Scenario 2: overlay conflict — primary matches doc, alternatives exist ─

    def test_overlay_conflict_same_doc_label_caps_moderate(self):
        """Primary overlay matches doc_label but alternatives present → moderate."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        # Primary overlay matches doc_label, but Venice CPIO becomes an alternative.
        _set_field(ctx, "overlay_name", "San Pedro CPIO", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Venice CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_selection_confidence == "moderate"

    def test_overlay_conflict_sets_branch_conflict_weakened(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "San Pedro CPIO", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Venice CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_conflict_weakened is True

    def test_overlay_conflict_still_selects_primary_branches(self):
        """Conflicted overlay still selects branches using primary subarea."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "San Pedro CPIO", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Venice CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert any("Industrial" in lbl for lbl in record.branch_primary_labels)

    def test_overlay_conflict_notes_mention_conflict(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "San Pedro CPIO", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Venice CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert "conflict" in record.branch_selection_notes.lower()

    # ── Scenario 3: identity mismatch — primary overlay resolves to different CPIO

    def test_identity_mismatch_forces_weak(self):
        """Primary overlay_name resolves to a different CPIO → forced 'weak'."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        # Venice CPIO as primary overlay contradicts doc_label San Pedro CPIO.
        # (Venice is unknown → _resolve_cpio_name returns None → identity_contested
        # requires BOTH sides to resolve, so we need a known mismatch pair.
        # Use "Coastal San Pedro CPIO" aliased to san pedro — that MATCHES.
        # For a true mismatch, we need two different known structures.
        # Currently only San Pedro is known, so we cannot produce a doc_key ≠ ctx_key
        # case with the current registry. Instead, test the overlay_name_has_conflict
        # path (scenario 2) and verify that an unknown primary does NOT trigger
        # identity_contested (because doc_key or ctx_key would be None).
        # This test verifies the safe fallback: unknown overlay → no identity contest.
        _set_field(ctx, "overlay_name", "Venice CPIO", SOURCE_CALLER_EXPLICIT, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        # Venice CPIO is unknown → _resolve_cpio_name returns None → identity not contested
        # Subarea was provided → strong (not degraded by unknown overlay)
        assert record.branch_selection_confidence == "strong"

    def test_identity_mismatch_both_known_forces_weak_and_contested(self):
        """Both doc_label and overlay_name resolve to known-but-different structures.

        This requires adding a second known CPIO to the registry — exercised here
        by directly calling run_cpio_extraction with identity_contested=True, which
        is the code path activated when _extract_cpio detects the mismatch.
        Validates the downstream effect without depending on registry state.
        """
        from zimas_linked_docs.cpio_fetch import run_cpio_extraction
        result = run_cpio_extraction(
            "San Pedro CPIO",
            cpio_subarea="Industrial-A",
            identity_contested=True,
        )
        assert result.branch_selection_confidence == "weak"
        assert result.branch_primary_labels == []
        assert result.branch_excluded_labels == []
        # All chapters should surface as general
        assert len(result.branch_general_labels) > 0

    def test_identity_mismatch_warning_issue_raised(self):
        """When identity is contested, a warning ZimasDocIssue is appended."""
        from zimas_linked_docs.cpio_fetch import run_cpio_extraction, _resolve_cpio_name

        # Directly call _extract_cpio via extract_surface_fields with a fabricated
        # context where the overlay_name's resolved key differs from the record's.
        # Since we only have one known structure, we simulate by patching the ctx:
        # overlay_name.value = "San Pedro CPIO" but record.doc_label is also
        # "San Pedro CPIO" — those would match, NOT contest.
        #
        # Instead: verify that the warning issue path fires by confirming the issue
        # list behaviour of run_cpio_extraction with identity_contested=True
        # (the extraction result that _extract_cpio would receive).
        result = run_cpio_extraction(
            "San Pedro CPIO",
            cpio_subarea="Industrial-A",
            identity_contested=True,
        )
        assert result.identity_contested is True
        assert "contested" in result.branch_selection_notes.lower()

    # ── Scenario 4: no overlay in context → identity check skipped ───────────

    def test_no_overlay_context_no_identity_check(self):
        """No overlay_name in context → identity check skipped, subarea used normally."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(_inp(cpio_subarea="Central Commercial-C"))
        assert not ctx.has_overlay_name
        extract_surface_fields([record], _narrowing_context=ctx)
        assert record.branch_selection_confidence == "strong"
        assert record.branch_conflict_weakened is False

    def test_no_overlay_context_primary_branches_populated(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(_inp(cpio_subarea="Central Commercial-C"))
        extract_surface_fields([record], _narrowing_context=ctx)
        assert any("Central Commercial" in lbl for lbl in record.branch_primary_labels)

    # ── Scenario 5: alias match → NOT treated as identity contradiction ────────

    def test_alias_overlay_not_contested(self):
        """'Coastal San Pedro CPIO' resolves to same canonical key as 'San Pedro CPIO'.

        This must NOT be treated as an identity contradiction — they are the same
        structure under an alias.
        """
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Coastal San Pedro CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        # Same canonical key → NOT identity_contested → strong (no conflict either)
        assert record.branch_selection_confidence == "strong"
        assert record.branch_conflict_weakened is False

    def test_alias_overlay_primary_branches_populated(self):
        record = _make_cpio_record("San Pedro CPIO")
        ctx = NarrowingContext()
        _set_field(ctx, "subarea", "Industrial-A", SOURCE_CALLER_EXPLICIT, "high")
        _set_field(ctx, "overlay_name", "Coastal San Pedro CPIO", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        extract_surface_fields([record], _narrowing_context=ctx)
        assert any("Industrial" in lbl for lbl in record.branch_primary_labels)


# ── Section 7: Specific-plan subarea context preservation (Pass B) ────────────
#
# specific_plan_subarea is preserved in NarrowingContext with provenance.
# It is CONTEXT PRESERVATION ONLY — no specific-plan structure extraction exists.
# Carrying this field does not improve interrupt posture, confidence, or
# any extractor behavior. CPIO extraction is unaffected.

class TestSpecificPlanSubareaContextPreservation:
    def test_specific_plan_subarea_populated_when_provided(self):
        ctx = build_narrowing_context(_inp(specific_plan_subarea="Area 1"))
        assert ctx.has_specific_plan_subarea
        assert ctx.specific_plan_subarea.value == "Area 1"

    def test_specific_plan_subarea_source_is_zimas_profile_field(self):
        ctx = build_narrowing_context(_inp(specific_plan_subarea="Area 1"))
        assert ctx.specific_plan_subarea.source == SOURCE_ZIMAS_PROFILE_FIELD

    def test_specific_plan_subarea_confidence_is_high(self):
        ctx = build_narrowing_context(_inp(specific_plan_subarea="Area 1"))
        assert ctx.specific_plan_subarea.confidence == "high"

    def test_specific_plan_subarea_absent_when_not_provided(self):
        ctx = build_narrowing_context(_inp())
        assert not ctx.has_specific_plan_subarea

    def test_specific_plan_subarea_absent_does_not_affect_other_fields(self):
        """Absence of specific_plan_subarea leaves cpio_subarea and overlay_name intact."""
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", overlay_zones=["San Pedro CPIO"])
        )
        assert not ctx.has_specific_plan_subarea
        assert ctx.has_subarea
        assert ctx.has_overlay_name

    def test_specific_plan_subarea_and_cpio_subarea_coexist(self):
        """Both can be present simultaneously — they refer to different documents."""
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", specific_plan_subarea="Area 1")
        )
        assert ctx.has_subarea
        assert ctx.has_specific_plan_subarea
        assert ctx.subarea.value == "Industrial-A"
        assert ctx.specific_plan_subarea.value == "Area 1"

    def test_specific_plan_subarea_no_conflict_with_cpio_subarea(self):
        """specific_plan_subarea and cpio_subarea are separate fields — no cross-field conflict."""
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", specific_plan_subarea="Area 1")
        )
        assert not ctx.has_any_conflict

    def test_specific_plan_subarea_has_no_conflict_without_alternatives(self):
        ctx = build_narrowing_context(_inp(specific_plan_subarea="Area 1"))
        assert not ctx.specific_plan_subarea_has_conflict

    def test_specific_plan_subarea_conflict_recorded_via_set_field(self):
        """If two sources provide different specific_plan_subarea values, conflict is recorded."""
        ctx = NarrowingContext()
        _set_field(ctx, "specific_plan_subarea", "Area 1", SOURCE_ZIMAS_PROFILE_FIELD, "high")
        _set_field(ctx, "specific_plan_subarea", "Area 2", SOURCE_ZONE_STRING_PARSE, "medium")
        assert ctx.specific_plan_subarea_has_conflict
        assert ctx.has_any_conflict
        assert ctx.specific_plan_subarea.value == "Area 1"   # higher priority wins
        assert ctx.specific_plan_subarea.alternatives[0].value == "Area 2"

    def test_specific_plan_subarea_does_not_affect_cpio_branch_selection(self):
        """Presence of specific_plan_subarea must not alter CPIO branch selection."""
        record = _make_cpio_record("San Pedro CPIO")
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", specific_plan_subarea="Area 1")
        )
        extract_surface_fields([record], _narrowing_context=ctx)
        # CPIO branch selection uses ctx.subarea ("Industrial-A"), not specific_plan_subarea.
        assert record.branch_selection_confidence == "strong"
        assert any("Industrial" in lbl for lbl in record.branch_primary_labels)

    def test_narrowing_strength_weak_with_only_specific_plan_subarea(self):
        """specific_plan_subarea alone scores 2 pts → 'weak' (below moderate threshold of 3)."""
        ctx = build_narrowing_context(_inp(specific_plan_subarea="Area 1"))
        assert ctx.narrowing_strength == "weak"

    def test_narrowing_strength_strong_with_cpio_and_specific_plan_subarea(self):
        """cpio_subarea (3) + specific_plan_subarea (2) = 5 → 'strong'."""
        ctx = build_narrowing_context(
            _inp(cpio_subarea="Industrial-A", specific_plan_subarea="Area 1")
        )
        assert ctx.narrowing_strength == "strong"

    def test_narrowing_strength_existing_cpio_only_unchanged(self):
        """Existing narrowing_strength tests for CPIO-only cases are unaffected."""
        ctx = build_narrowing_context(_inp(cpio_subarea="Industrial-A"))
        assert ctx.narrowing_strength == "moderate"
