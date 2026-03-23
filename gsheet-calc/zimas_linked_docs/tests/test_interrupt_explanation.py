"""Tests for interrupt-decision rigor explanation (Pass F).

Verifies that:
1. Each InterruptDecision carries a triggering_rigor list with one TriggerSummary
   per triggering record
2. _rigor_level() correctly classifies records by confidence_state and
   doc_type_confidence into the five RIGOR_* levels
3. _rigor_detail() returns doc-type-appropriate plain-English descriptions
4. Multiple authority classes on the same topic each get their own rigor entry
5. One strong + one weak authority on the same topic produce different rigor levels
6. A clean topic (INTERRUPT_NONE) has empty triggering_rigor
7. The reason string includes inline rigor tags per triggering label
8. No regression on existing interrupt level decisions

NOTE: triggering_rigor is informational only. It does not change which topics
interrupt or at what level — that logic is unchanged.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_CASE_ZA,
    DOC_TYPE_CASE_CPC,
    DOC_TYPE_CASE_ENV,
    DOC_TYPE_OVERLAY_CDO,
    DOC_TYPE_OVERLAY_HA,
    DOC_TYPE_UNKNOWN_ARTIFACT,
    INTERRUPT_NONE,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_UNRESOLVED,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    POSTURE_MANUAL_REVIEW_FIRST,
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_SURFACE_USABLE,
    CONF_FETCHED_PARTIALLY_USABLE,
    CONF_REFUSE_TO_DECIDE,
    RIGOR_DETECTION_ONLY,
    RIGOR_IDENTITY_CONFIRMED,
    RIGOR_STRUCTURALLY_NARROWED,
    RIGOR_DOCUMENT_BACKED,
    RIGOR_AMBIGUOUS_IDENTITY,
    TriggerSummary,
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasLinkedDocInput,
)
from zimas_linked_docs.gatekeeper import evaluate_interrupts, _rigor_level, _build_trigger_summary
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _record(
    doc_type: str,
    doc_label: str,
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
    doc_type_confidence: str = "confirmed",
    usability_posture: str = POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    extracted_chapter_list: list[str] | None = None,
    record_id: str = "test-001",
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id=record_id,
        doc_type=doc_type,
        doc_label=doc_label,
        usability_posture=usability_posture,
        confidence_state=confidence_state,
        doc_type_confidence=doc_type_confidence,
        extracted_chapter_list=extracted_chapter_list or [],
    )


def _registry(*records: LinkedDocRecord) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=list(records))


def _decisions_for(*records: LinkedDocRecord, topics=None) -> list:
    if topics is None:
        topics = ["FAR", "density", "height", "parking", "setback"]
    decisions, _ = evaluate_interrupts(_registry(*records), topics=topics)
    return decisions


def _rigor_for_topic(decisions, topic: str) -> list[TriggerSummary]:
    d = next(d for d in decisions if d.topic == topic)
    return d.triggering_rigor


def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


# ── Section 1: _rigor_level() unit tests ─────────────────────────────────────

class TestRigorLevelClassification:
    def test_detected_not_interpreted_is_detection_only(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q", confidence_state=CONF_DETECTED_NOT_INTERPRETED)
        assert _rigor_level(r) == RIGOR_DETECTION_ONLY

    def test_surface_usable_without_chapters_is_identity_confirmed(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q-Ord-186481",
                    confidence_state=CONF_SURFACE_USABLE)
        assert _rigor_level(r) == RIGOR_IDENTITY_CONFIRMED

    def test_surface_usable_with_chapters_is_structurally_narrowed(self):
        r = _record(DOC_TYPE_OVERLAY_CPIO, "San Pedro CPIO",
                    confidence_state=CONF_SURFACE_USABLE,
                    extracted_chapter_list=["Chapter I: General", "Chapter VI: Industrial"])
        assert _rigor_level(r) == RIGOR_STRUCTURALLY_NARROWED

    def test_fetched_partially_usable_is_document_backed(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q",
                    confidence_state=CONF_FETCHED_PARTIALLY_USABLE)
        assert _rigor_level(r) == RIGOR_DOCUMENT_BACKED

    def test_ambiguous_doc_type_confidence_is_ambiguous_identity(self):
        r = _record(DOC_TYPE_UNKNOWN_ARTIFACT, "Unknown-Overlay",
                    doc_type_confidence="ambiguous")
        assert _rigor_level(r) == RIGOR_AMBIGUOUS_IDENTITY

    def test_refuse_to_decide_confidence_state_is_ambiguous_identity(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q", confidence_state=CONF_REFUSE_TO_DECIDE)
        assert _rigor_level(r) == RIGOR_AMBIGUOUS_IDENTITY

    def test_specific_plan_confirmed_is_identity_confirmed(self):
        r = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice Specific Plan",
                    confidence_state=CONF_SURFACE_USABLE, doc_type_confidence="confirmed")
        assert _rigor_level(r) == RIGOR_IDENTITY_CONFIRMED

    def test_specific_plan_provisional_is_detection_only(self):
        r = _record(DOC_TYPE_SPECIFIC_PLAN, "SPECIFIC PLAN",
                    confidence_state=CONF_DETECTED_NOT_INTERPRETED,
                    doc_type_confidence="provisional")
        assert _rigor_level(r) == RIGOR_DETECTION_ONLY


# ── Section 2: TriggerSummary content ────────────────────────────────────────

class TestTriggerSummaryContent:
    def test_trigger_summary_fields_populated(self):
        r = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                    confidence_state=CONF_SURFACE_USABLE, record_id="sp-001")
        ts = _build_trigger_summary(r)
        assert ts.record_id == "sp-001"
        assert ts.doc_label == "Venice SP"
        assert ts.doc_type == DOC_TYPE_SPECIFIC_PLAN
        assert ts.rigor_level == RIGOR_IDENTITY_CONFIRMED
        assert ts.rigor_detail  # non-empty

    def test_rigor_detail_mentions_plan_name_confirmed_for_sp(self):
        r = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                    confidence_state=CONF_SURFACE_USABLE)
        ts = _build_trigger_summary(r)
        assert "plan name confirmed" in ts.rigor_detail.lower()

    def test_rigor_detail_mentions_case_conditions_for_za(self):
        r = _record(DOC_TYPE_CASE_ZA, "ZA-2014-123")
        ts = _build_trigger_summary(r)
        assert "conditions not reviewed" in ts.rigor_detail.lower()

    def test_rigor_detail_mentions_ordinance_for_q_identity_confirmed(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q-Ord-186481",
                    confidence_state=CONF_SURFACE_USABLE)
        ts = _build_trigger_summary(r)
        assert "ordinance number confirmed" in ts.rigor_detail.lower()

    def test_rigor_detail_mentions_scope_unknown_for_q_detection_only(self):
        r = _record(DOC_TYPE_Q_CONDITION, "Q")
        ts = _build_trigger_summary(r)
        assert "scope unknown" in ts.rigor_detail.lower()

    def test_rigor_detail_mentions_chapters_for_structurally_narrowed(self):
        r = _record(DOC_TYPE_OVERLAY_CPIO, "San Pedro CPIO",
                    confidence_state=CONF_SURFACE_USABLE,
                    extracted_chapter_list=["Chapter I"])
        ts = _build_trigger_summary(r)
        assert "chapter structure confirmed" in ts.rigor_detail.lower()


# ── Section 3: triggering_rigor on InterruptDecision ─────────────────────────

class TestInterruptDecisionRigor:
    def test_interrupt_none_has_empty_triggering_rigor(self):
        """Clean topic with no authority items — triggering_rigor is empty."""
        decisions = _decisions_for(topics=["FAR"])
        far = next(d for d in decisions if d.topic == "FAR")
        assert far.interrupt_level == INTERRUPT_NONE
        assert far.triggering_rigor == []

    def test_single_authority_produces_one_rigor_entry(self):
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                     confidence_state=CONF_SURFACE_USABLE)
        decisions = _decisions_for(sp, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        assert len(rigor) == 1
        assert rigor[0].doc_type == DOC_TYPE_SPECIFIC_PLAN
        assert rigor[0].rigor_level == RIGOR_IDENTITY_CONFIRMED

    def test_multiple_authorities_each_get_rigor_entry(self):
        """Two authorities on FAR → two TriggerSummary entries."""
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                     confidence_state=CONF_SURFACE_USABLE, record_id="sp-001")
        za = _record(DOC_TYPE_CASE_ZA, "ZA-2014-001",
                     confidence_state=CONF_DETECTED_NOT_INTERPRETED, record_id="za-001")
        decisions = _decisions_for(sp, za, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        assert len(rigor) == 2
        rigor_levels = {rs.rigor_level for rs in rigor}
        assert RIGOR_IDENTITY_CONFIRMED in rigor_levels
        assert RIGOR_DETECTION_ONLY in rigor_levels

    def test_strong_and_weak_authority_show_different_rigor_levels(self):
        """One identity-confirmed + one detection-only → rigor levels differ."""
        q_strong = _record(DOC_TYPE_Q_CONDITION, "Q-Ord-186481",
                           confidence_state=CONF_SURFACE_USABLE, record_id="q-strong")
        q_weak = _record(DOC_TYPE_Q_CONDITION, "Q",
                         confidence_state=CONF_DETECTED_NOT_INTERPRETED, record_id="q-weak")
        decisions = _decisions_for(q_strong, q_weak, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        levels = {rs.rigor_level for rs in rigor}
        assert RIGOR_IDENTITY_CONFIRMED in levels
        assert RIGOR_DETECTION_ONLY in levels

    def test_structurally_narrowed_cpio_shown_in_rigor(self):
        cpio = _record(DOC_TYPE_OVERLAY_CPIO, "San Pedro CPIO",
                       confidence_state=CONF_SURFACE_USABLE,
                       extracted_chapter_list=["Chapter I", "Chapter VI"],
                       usability_posture=POSTURE_MANUAL_REVIEW_FIRST)
        decisions = _decisions_for(cpio, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        assert rigor[0].rigor_level == RIGOR_STRUCTURALLY_NARROWED

    def test_document_backed_record_shown_in_rigor(self):
        backed = _record(DOC_TYPE_Q_CONDITION, "Q",
                         confidence_state=CONF_FETCHED_PARTIALLY_USABLE)
        decisions = _decisions_for(backed, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        assert rigor[0].rigor_level == RIGOR_DOCUMENT_BACKED

    def test_ambiguous_record_shown_in_rigor(self):
        amb = _record(DOC_TYPE_UNKNOWN_ARTIFACT, "UNKNOWN-OVERLAY",
                      doc_type_confidence="ambiguous")
        decisions = _decisions_for(amb, topics=["FAR"])
        rigor = _rigor_for_topic(decisions, "FAR")
        assert rigor[0].rigor_level == RIGOR_AMBIGUOUS_IDENTITY

    def test_rigor_entries_match_triggering_record_ids(self):
        """triggering_rigor entries correspond 1-1 with triggering_record_ids."""
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Hollywood SP",
                     confidence_state=CONF_SURFACE_USABLE, record_id="sp-001")
        decisions = _decisions_for(sp, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert len(d.triggering_rigor) == len(d.triggering_record_ids)
        assert d.triggering_rigor[0].record_id == d.triggering_record_ids[0]


# ── Section 4: reason string includes rigor tags ──────────────────────────────

class TestReasonStringRigorTags:
    def test_reason_includes_rigor_tag_for_single_authority(self):
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                     confidence_state=CONF_SURFACE_USABLE)
        decisions = _decisions_for(sp, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert "[identity_confirmed]" in d.reason

    def test_reason_includes_detection_only_tag(self):
        za = _record(DOC_TYPE_CASE_ZA, "ZA-2014-001")
        decisions = _decisions_for(za, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert "[detection_only]" in d.reason

    def test_reason_includes_rigor_tag_for_each_triggering_authority(self):
        """Both authorities should appear with rigor tags in the reason string."""
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                     confidence_state=CONF_SURFACE_USABLE, record_id="sp-001")
        q = _record(DOC_TYPE_Q_CONDITION, "Q",
                    confidence_state=CONF_DETECTED_NOT_INTERPRETED, record_id="q-001")
        decisions = _decisions_for(sp, q, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert "[identity_confirmed]" in d.reason
        assert "[detection_only]" in d.reason

    def test_interrupt_none_reason_has_no_rigor_tags(self):
        decisions = _decisions_for(topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert d.interrupt_level == INTERRUPT_NONE
        assert "[" not in d.reason or "WARNING" in d.reason  # only coverage warnings use brackets


# ── Section 5: End-to-end pipeline ────────────────────────────────────────────

class TestPipelineRigorEndToEnd:
    def test_named_specific_plan_rigor_is_identity_confirmed_in_pipeline(self):
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.triggering_rigor
        assert far_decision.triggering_rigor[0].rigor_level == RIGOR_IDENTITY_CONFIRMED

    def test_q_with_ordinance_rigor_is_identity_confirmed_in_pipeline(self):
        inp = _inp(
            q_conditions=["Q"],
            q_ordinance_number="186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.triggering_rigor
        assert far_decision.triggering_rigor[0].rigor_level == RIGOR_IDENTITY_CONFIRMED

    def test_bare_q_rigor_is_detection_only_in_pipeline(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.triggering_rigor
        assert far_decision.triggering_rigor[0].rigor_level == RIGOR_DETECTION_ONLY


# ── Section 6: No regression on interrupt decisions ──────────────────────────

class TestNoRegressionInterruptDecisions:
    def test_specific_plan_still_unresolved_and_blocking(self):
        sp = _record(DOC_TYPE_SPECIFIC_PLAN, "Venice SP",
                     confidence_state=CONF_SURFACE_USABLE)
        decisions = _decisions_for(sp, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert d.interrupt_level == INTERRUPT_UNRESOLVED
        assert d.blocking

    def test_q_still_provisional_not_blocking(self):
        q = _record(DOC_TYPE_Q_CONDITION, "Q", confidence_state=CONF_SURFACE_USABLE)
        decisions = _decisions_for(q, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert d.interrupt_level == INTERRUPT_PROVISIONAL
        assert not d.blocking

    def test_cpio_unresolved_without_structure(self):
        cpio = _record(DOC_TYPE_OVERLAY_CPIO, "Venice CPIO",
                       confidence_state=CONF_DETECTED_NOT_INTERPRETED,
                       usability_posture=POSTURE_MANUAL_REVIEW_FIRST)
        decisions = _decisions_for(cpio, topics=["FAR"])
        d = next(x for x in decisions if x.topic == "FAR")
        assert d.interrupt_level == INTERRUPT_UNRESOLVED

    def test_cdo_provisional_on_all_topics(self):
        cdo = _record(DOC_TYPE_OVERLAY_CDO, "CDO",
                      usability_posture=POSTURE_MANUAL_REVIEW_FIRST)
        decisions = _decisions_for(cdo, topics=["FAR", "density", "height", "parking", "setback"])
        for d in decisions:
            assert d.interrupt_level == INTERRUPT_PROVISIONAL, d.topic

    def test_env_case_still_not_interrupting_parking(self):
        env = _record(DOC_TYPE_CASE_ENV, "ENV-2018-345")
        decisions = _decisions_for(env, topics=["FAR", "density", "parking"])
        levels = {d.topic: d.interrupt_level for d in decisions}
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL
        assert levels["parking"] == INTERRUPT_NONE
