"""Tests for specific-plan identity handling (Pass E).

Verifies that:
1. A confirmed plan name (from Site.specific_plan structured field) upgrades to
   CONF_SURFACE_USABLE — identity is known, content is not interpreted
2. A plan inferred from overlay_zones text stays at CONF_DETECTED_NOT_INTERPRETED —
   doc_type_confidence == "provisional" does not earn the upgrade
3. When specific_plan_subarea is provided alongside specific_plan, exactly ONE
   LinkedDocRecord is produced (deduplication merges both into the plan-name record),
   with both source fields preserved in detected_from_fields
4. Interrupt posture remains INTERRUPT_UNRESOLVED on all topics regardless of
   confidence_state — identity upgrade does not soften interrupts
5. No regression on CPIO, Q/D, case document, SUD subtype handling

NOTE: CONF_SURFACE_USABLE on a specific plan means "plan name confirmed from
ZIMAS-verified source." It does NOT mean the plan has been interpreted or that
any of its standards have been read.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    INTERRUPT_UNRESOLVED,
    INTERRUPT_PROVISIONAL,
    CONF_SURFACE_USABLE,
    CONF_DETECTED_NOT_INTERPRETED,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    LinkedDocRecord,
    LinkedDocRegistry,
    LinkedDocCandidate,
    ZimasLinkedDocInput,
    PATTERN_SPECIFIC_PLAN_FIELD,
    PATTERN_OVERLAY_NAME_FIELD,
)
from zimas_linked_docs.doc_classifier import classify_candidates
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.gatekeeper import evaluate_interrupts
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _run_confidence(records: list[LinkedDocRecord]) -> list[LinkedDocRecord]:
    records, _ = assign_confidence_states(records)
    return records


def _sp_candidate(
    raw: str,
    pattern: str = PATTERN_SPECIFIC_PLAN_FIELD,
    source_field: str = "specific_plan",
) -> LinkedDocCandidate:
    return LinkedDocCandidate(
        candidate_id=f"test-sp-{raw.lower().replace(' ', '-')}",
        source_field=source_field,
        raw_value=raw,
        detected_pattern=pattern,
    )


def _registry_with_record(record: LinkedDocRecord) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=[record])


# ── Section 1: Deduplication — name + subarea → one record ────────────────────

class TestSpecificPlanDeduplication:
    def test_plan_only_produces_one_record(self):
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert len(sp_records) == 1

    def test_plan_with_subarea_produces_one_record(self):
        """name + subarea must merge into a single record, not two."""
        inp = _inp(specific_plan="Venice Specific Plan", specific_plan_subarea="Area A")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert len(sp_records) == 1, (
            f"Expected 1 specific plan record, got {len(sp_records)}: "
            f"{[r.doc_label for r in sp_records]}"
        )

    def test_plan_label_is_plan_name_only_not_combined_string(self):
        """doc_label must be the plan name, not 'Plan Name / Subarea'."""
        inp = _inp(specific_plan="Venice Specific Plan", specific_plan_subarea="Area A")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert sp_records[0].doc_label == "Venice Specific Plan"
        assert "/" not in sp_records[0].doc_label

    def test_plan_with_subarea_preserves_both_source_fields_in_provenance(self):
        """detected_from_fields should include both 'specific_plan' and
        'specific_plan_subarea' so callers know the subarea was present."""
        inp = _inp(specific_plan="Venice Specific Plan", specific_plan_subarea="Area A")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        fields = sp_records[0].detected_from_fields
        assert "specific_plan" in fields
        assert "specific_plan_subarea" in fields

    def test_plan_without_subarea_has_only_specific_plan_source_field(self):
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert sp_records[0].detected_from_fields == ["specific_plan"]


# ── Section 2: Confidence upgrade — confirmed identity ────────────────────────

class TestSpecificPlanConfidenceUpgrade:
    def _classify_and_upgrade(
        self,
        raw: str,
        pattern: str = PATTERN_SPECIFIC_PLAN_FIELD,
        source_field: str = "specific_plan",
    ) -> LinkedDocRecord:
        candidates = [_sp_candidate(raw, pattern=pattern, source_field=source_field)]
        records, _ = classify_candidates(candidates)
        return _run_confidence(records)[0]

    def test_confirmed_plan_name_upgrades_to_surface_usable(self):
        """Named plan from structured field → doc_type_confidence='confirmed' → surface_usable."""
        record = self._classify_and_upgrade("Venice Specific Plan")
        assert record.doc_type_confidence == "confirmed"
        assert record.confidence_state == CONF_SURFACE_USABLE

    def test_extraction_notes_set_for_confirmed_plan(self):
        record = self._classify_and_upgrade("Hollywood Specific Plan")
        assert "Hollywood Specific Plan" in record.extraction_notes
        assert "not fetched or interpreted" in record.extraction_notes.lower()

    def test_provisional_plan_stays_at_detected_not_interpreted(self):
        """Plan name from overlay_zones heuristic → 'provisional' → no upgrade."""
        candidates = [_sp_candidate(
            "Venice Specific Plan",
            pattern=PATTERN_OVERLAY_NAME_FIELD,
            source_field="overlay_zones",
        )]
        records, _ = classify_candidates(candidates)
        assert records[0].doc_type_confidence == "provisional"
        upgraded = _run_confidence(records)
        assert upgraded[0].confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_generic_specific_plan_string_stays_at_detected_not_interpreted(self):
        """A bare 'SPECIFIC PLAN' string from overlay_zones has no confirmed name → no upgrade."""
        candidates = [_sp_candidate(
            "SPECIFIC PLAN",
            pattern=PATTERN_OVERLAY_NAME_FIELD,
            source_field="overlay_zones",
        )]
        records, _ = classify_candidates(candidates)
        upgraded = _run_confidence(records)
        assert upgraded[0].confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_confirmed_plan_extraction_notes_not_set_for_provisional(self):
        """Provisional detection must not set extraction_notes (no false confidence)."""
        candidates = [_sp_candidate(
            "Venice Specific Plan",
            pattern=PATTERN_OVERLAY_NAME_FIELD,
            source_field="overlay_zones",
        )]
        records, _ = classify_candidates(candidates)
        upgraded = _run_confidence(records)
        assert upgraded[0].extraction_notes == ""


# ── Section 3: Interrupt posture unchanged ────────────────────────────────────

class TestSpecificPlanInterruptUnchanged:
    def _decisions(self, confidence_state: str) -> dict[str, str]:
        record = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            confidence_state=confidence_state,
        )
        decisions, _ = evaluate_interrupts(
            _registry_with_record(record),
            topics=["FAR", "density", "height", "parking", "setback"],
        )
        return {d.topic: d.interrupt_level for d in decisions}

    def test_surface_usable_specific_plan_still_produces_unresolved(self):
        """Confidence upgrade must not soften the interrupt — still UNRESOLVED."""
        levels = self._decisions(CONF_SURFACE_USABLE)
        for topic in ("FAR", "density", "height", "parking", "setback"):
            assert levels[topic] == INTERRUPT_UNRESOLVED, topic

    def test_detected_not_interpreted_specific_plan_produces_unresolved(self):
        levels = self._decisions(CONF_DETECTED_NOT_INTERPRETED)
        for topic in ("FAR", "density", "height", "parking", "setback"):
            assert levels[topic] == INTERRUPT_UNRESOLVED, topic

    def test_specific_plan_interrupt_is_blocking(self):
        record = LinkedDocRecord(
            record_id="test-sp-002",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            confidence_state=CONF_SURFACE_USABLE,
        )
        decisions, _ = evaluate_interrupts(_registry_with_record(record), topics=["FAR"])
        assert decisions[0].blocking


# ── Section 4: End-to-end pipeline ────────────────────────────────────────────

class TestPipelineEndToEnd:
    def test_named_plan_produces_surface_usable_in_pipeline(self):
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert sp_records
        assert sp_records[0].confidence_state == CONF_SURFACE_USABLE

    def test_named_plan_with_subarea_still_interrupts_all_topics(self):
        inp = _inp(specific_plan="Venice Specific Plan", specific_plan_subarea="Area A")
        output = run_zimas_linked_doc_pipeline(inp)
        for decision in output.interrupt_decisions:
            assert decision.interrupt_level == INTERRUPT_UNRESOLVED, decision.topic

    def test_named_plan_with_subarea_single_record_and_interrupts(self):
        inp = _inp(specific_plan="Hollywood Specific Plan", specific_plan_subarea="Sub-Area 3")
        output = run_zimas_linked_doc_pipeline(inp)
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert len(sp_records) == 1
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_UNRESOLVED
        assert far_decision.blocking


# ── Section 5: No regression on other authority classes ──────────────────────

class TestNoRegressionOtherDocTypes:
    def test_q_condition_still_interrupts_provisionally(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL
        assert not far_decision.blocking

    def test_cpio_still_classifies_correctly(self):
        inp = _inp(overlay_zones=["San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        assert cpio_records

    def test_specific_plan_and_cpio_coexist(self):
        """A parcel can have both a specific plan and a CPIO."""
        inp = _inp(
            specific_plan="Venice Specific Plan",
            overlay_zones=["San Pedro CPIO"],
        )
        output = run_zimas_linked_doc_pipeline(inp)
        types = {r.doc_type for r in output.registry.records}
        assert DOC_TYPE_SPECIFIC_PLAN in types
        assert DOC_TYPE_OVERLAY_CPIO in types

    def test_specific_plan_confidence_does_not_affect_cpio_confidence(self):
        """Confidence upgrade for SP is isolated — does not contaminate CPIO record.

        San Pedro CPIO gets structure extracted (known-structure path, no HTTP)
        and reaches surface_usable regardless of cpio_subarea. Without subarea,
        branch selection is "uncertain" but the structure itself is confirmed.
        SP still gets surface_usable from its own confirmed-identity path.
        The two confidence upgrades are fully independent.

        NOTE: The assertion here changed after Pass J (CPIO known-structure extraction
        was moved before the fetch_decision gate). Prior to that fix, CPIO silently
        skipped extraction when fetch_decision=FETCH_DEFER, staying at
        detected_not_interpreted. Now it correctly extracts in all cases.
        """
        inp = _inp(
            specific_plan="Venice Specific Plan",
            overlay_zones=["San Pedro CPIO"],
        )
        output = run_zimas_linked_doc_pipeline(inp)
        cpio_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_OVERLAY_CPIO]
        # San Pedro CPIO: structure extracted (known-structure registry), reaches surface_usable
        assert cpio_records[0].confidence_state == CONF_SURFACE_USABLE
        # SP independently gets surface_usable from its own confirmed-identity rule
        sp_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_SPECIFIC_PLAN]
        assert sp_records[0].confidence_state == CONF_SURFACE_USABLE
