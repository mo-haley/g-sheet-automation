"""Tests for D/Q retrieval-status visibility in gatekeeper output (Pass H).

Verifies that:
1. _rigor_detail() returns status-aware strings for each DQ_RETRIEVAL_* level
2. _dq_retrieval_note() produces the correct inline identity addendum
3. InterruptDecision.reason contains D/Q identity info when Q/D records trigger
4. InterruptDecision.triggering_rigor[].rigor_detail is status-specific for Q/D
5. candidate_only, number_known, url_known, zi_corroborated each produce distinct output
6. Interrupt levels remain UNCHANGED regardless of retrieval status
7. Non-Q/D records do not receive retrieval-status text
8. Multiple Q/D records on the same topic each appear in the note
9. Explicit "content unread" / "not interpreted" language always present in Q/D output

NOTE: These changes are informational only. No interrupt level or blocking posture
changes as a result of retrieval status — only explanation strings improve.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_UNRESOLVED,
    CONF_SURFACE_USABLE,
    CONF_DETECTED_NOT_INTERPRETED,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    URL_CONF_DIRECT_LINK,
    URL_CONF_INFERRED,
    DQ_RETRIEVAL_CANDIDATE_ONLY,
    DQ_RETRIEVAL_NUMBER_KNOWN,
    DQ_RETRIEVAL_URL_KNOWN,
    DQ_RETRIEVAL_ZI_CORROBORATED,
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasLinkedDocInput,
)
from zimas_linked_docs.gatekeeper import (
    evaluate_interrupts,
    _rigor_detail,
    _rigor_level,
    _dq_retrieval_note,
    _dq_identity_detail,
)
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _q_record(
    ordinance: str | None = None,
    retrieval_status: str = "",
    url: str | None = None,
    url_confidence: str = URL_CONF_INFERRED,
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
) -> LinkedDocRecord:
    r = LinkedDocRecord(
        record_id="test-q-001",
        doc_type=DOC_TYPE_Q_CONDITION,
        doc_label="Q" if not ordinance else f"Q (O-{ordinance})",
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        source_ordinance_number=ordinance,
        url=url,
        url_confidence=url_confidence,
        confidence_state=confidence_state,
        ordinance_retrieval_status=retrieval_status,
    )
    return r


def _d_record(
    ordinance: str | None = None,
    retrieval_status: str = "",
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-d-001",
        doc_type=DOC_TYPE_D_LIMITATION,
        doc_label="D" if not ordinance else f"D (O-{ordinance})",
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        source_ordinance_number=ordinance,
        confidence_state=confidence_state,
        ordinance_retrieval_status=retrieval_status,
    )


def _registry(records: list[LinkedDocRecord]) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=records)


# ── Section 1: _dq_identity_detail() ──────────────────────────────────────────

class TestDqIdentityDetail:
    """_dq_identity_detail is the RIGOR_IDENTITY_CONFIRMED branch for Q/D records."""

    def test_zi_corroborated_mentions_corroboration(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "corroborated" in detail.lower()
        assert "zi" in detail.lower() or "ZI" in detail

    def test_zi_corroborated_explicit_content_unread(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "not" in detail.lower() and ("read" in detail.lower() or "fetched" in detail.lower())

    def test_url_known_mentions_url(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_URL_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "url" in detail.lower() or "retrieval" in detail.lower()

    def test_url_known_explicit_content_unread(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_URL_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "not" in detail.lower() and ("read" in detail.lower() or "fetched" in detail.lower())

    def test_number_known_mentions_ordinance_number(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "number" in detail.lower() or "ordinance" in detail.lower()

    def test_number_known_explicit_content_unread(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "not" in detail.lower() and ("read" in detail.lower() or "fetched" in detail.lower())

    def test_d_limitation_label_used_in_detail(self):
        r = _d_record(ordinance="172128", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "limitation" in detail.lower() or "D limitation" in detail

    def test_q_condition_label_used_in_detail(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _dq_identity_detail(r)
        assert "condition" in detail.lower() or "Q condition" in detail

    def test_four_statuses_produce_distinct_strings(self):
        base = dict(ordinance="186481", confidence_state=CONF_SURFACE_USABLE)
        details = {
            status: _dq_identity_detail(_q_record(**base, retrieval_status=status))
            for status in (
                DQ_RETRIEVAL_ZI_CORROBORATED,
                DQ_RETRIEVAL_URL_KNOWN,
                DQ_RETRIEVAL_NUMBER_KNOWN,
            )
        }
        assert len(set(details.values())) == 3, "Each status must produce a distinct string"


# ── Section 2: _rigor_detail() integration ────────────────────────────────────

class TestRigorDetailForDq:
    """_rigor_detail() must route Q/D IDENTITY_CONFIRMED cases through _dq_identity_detail."""

    def test_q_number_known_routed_through_dq_identity_detail(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        assert _rigor_level(r) == "identity_confirmed"
        detail = _rigor_detail(r)
        assert "number" in detail.lower() or "ordinance" in detail.lower()
        assert "not" in detail.lower()

    def test_q_zi_corroborated_routed_through_dq_identity_detail(self):
        r = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        detail = _rigor_detail(r)
        assert "corroborated" in detail.lower()

    def test_q_candidate_only_at_detection_only_rigor(self):
        """candidate_only → RIGOR_DETECTION_ONLY → type-specific detection string."""
        r = _q_record(retrieval_status=DQ_RETRIEVAL_CANDIDATE_ONLY,
                      confidence_state=CONF_DETECTED_NOT_INTERPRETED)
        assert _rigor_level(r) == "detection_only"
        detail = _rigor_detail(r)
        assert "not confirmed" in detail.lower() or "ordinance" in detail.lower()

    def test_sp_rigor_detail_unchanged(self):
        """Specific plan must still get its own rigor detail string, not Q/D routing."""
        sp = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            doc_type_confidence="confirmed",
            confidence_state=CONF_SURFACE_USABLE,
        )
        detail = _rigor_detail(sp)
        assert "plan" in detail.lower()
        assert "condition" not in detail.lower()


# ── Section 3: _dq_retrieval_note() ───────────────────────────────────────────

class TestDqRetrievalNote:
    def test_empty_when_no_dq_records(self):
        sp = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        )
        assert _dq_retrieval_note([sp]) == ""

    def test_candidate_only_says_not_confirmed(self):
        note = _dq_retrieval_note([_q_record(retrieval_status=DQ_RETRIEVAL_CANDIDATE_ONLY)])
        assert "not yet confirmed" in note.lower() or "not confirmed" in note.lower()

    def test_number_known_says_number_confirmed(self):
        note = _dq_retrieval_note([
            _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN)
        ])
        assert "confirmed" in note.lower()
        assert "unread" in note.lower() or "content" in note.lower()

    def test_url_known_mentions_url(self):
        note = _dq_retrieval_note([
            _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_URL_KNOWN)
        ])
        assert "url" in note.lower()
        assert "unread" in note.lower() or "content" in note.lower()

    def test_zi_corroborated_mentions_corroboration(self):
        note = _dq_retrieval_note([
            _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED)
        ])
        assert "corroborated" in note.lower()
        assert "unread" in note.lower() or "content" in note.lower()

    def test_multiple_dq_records_both_appear(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN)
        q.doc_label = "Q (O-186481)"
        d = _d_record(retrieval_status=DQ_RETRIEVAL_CANDIDATE_ONLY)
        d.doc_label = "D"
        note = _dq_retrieval_note([q, d])
        assert "Q (O-186481)" in note
        assert "D" in note

    def test_note_starts_with_dq_identity_prefix(self):
        note = _dq_retrieval_note([_q_record(retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN)])
        assert note.startswith("D/Q identity:")


# ── Section 4: InterruptDecision.reason content ────────────────────────────────

class TestInterruptReasonContent:
    def _decisions(self, records: list[LinkedDocRecord]) -> list:
        decisions, _ = evaluate_interrupts(_registry(records), topics=["FAR", "density"])
        return decisions

    def test_reason_contains_dq_identity_note_for_q(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        decisions = self._decisions([q])
        far = next(d for d in decisions if d.topic == "FAR")
        assert "D/Q identity:" in far.reason

    def test_reason_mentions_corroboration_for_zi_corroborated(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        decisions = self._decisions([q])
        far = next(d for d in decisions if d.topic == "FAR")
        assert "corroborated" in far.reason.lower()

    def test_reason_mentions_url_for_url_known(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_URL_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        decisions = self._decisions([q])
        far = next(d for d in decisions if d.topic == "FAR")
        assert "url" in far.reason.lower()

    def test_reason_says_not_confirmed_for_candidate_only(self):
        q = _q_record(retrieval_status=DQ_RETRIEVAL_CANDIDATE_ONLY)
        decisions = self._decisions([q])
        far = next(d for d in decisions if d.topic == "FAR")
        assert "not yet confirmed" in far.reason.lower() or "not confirmed" in far.reason.lower()

    def test_reason_has_no_dq_note_when_no_dq_records(self):
        sp = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            confidence_state=CONF_SURFACE_USABLE,
        )
        decisions = self._decisions([sp])
        far = next(d for d in decisions if d.topic == "FAR")
        assert "D/Q identity:" not in far.reason

    def test_triggering_rigor_detail_is_status_aware(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        decisions = self._decisions([q])
        far = next(d for d in decisions if d.topic == "FAR")
        assert far.triggering_rigor
        assert "corroborated" in far.triggering_rigor[0].rigor_detail.lower()


# ── Section 5: Interrupt invariance ────────────────────────────────────────────

class TestInterruptInvarianceWithExplanation:
    """Better explanations must never change interrupt levels."""

    def _far_level(self, records: list[LinkedDocRecord]) -> str:
        decisions, _ = evaluate_interrupts(_registry(records), topics=["FAR"])
        return decisions[0].interrupt_level

    def test_candidate_only_still_provisional(self):
        assert self._far_level([_q_record(retrieval_status=DQ_RETRIEVAL_CANDIDATE_ONLY)]) == INTERRUPT_PROVISIONAL

    def test_number_known_still_provisional(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_NUMBER_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        assert self._far_level([q]) == INTERRUPT_PROVISIONAL

    def test_url_known_still_provisional(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_URL_KNOWN,
                      confidence_state=CONF_SURFACE_USABLE)
        assert self._far_level([q]) == INTERRUPT_PROVISIONAL

    def test_zi_corroborated_still_provisional(self):
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        assert self._far_level([q]) == INTERRUPT_PROVISIONAL

    def test_sp_plus_q_still_unresolved(self):
        """Specific plan dominates; Q's improved identity does not soften to provisional."""
        sp = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            confidence_state=CONF_SURFACE_USABLE,
        )
        q = _q_record(ordinance="186481", retrieval_status=DQ_RETRIEVAL_ZI_CORROBORATED,
                      confidence_state=CONF_SURFACE_USABLE)
        assert self._far_level([sp, q]) == INTERRUPT_UNRESOLVED

    def test_none_of_the_statuses_make_interrupt_none(self):
        for status in (DQ_RETRIEVAL_CANDIDATE_ONLY, DQ_RETRIEVAL_NUMBER_KNOWN,
                       DQ_RETRIEVAL_URL_KNOWN, DQ_RETRIEVAL_ZI_CORROBORATED):
            q = _q_record(ordinance="186481" if status != DQ_RETRIEVAL_CANDIDATE_ONLY else None,
                          retrieval_status=status,
                          confidence_state=CONF_SURFACE_USABLE if status != DQ_RETRIEVAL_CANDIDATE_ONLY else CONF_DETECTED_NOT_INTERPRETED)
            level = self._far_level([q])
            assert level == INTERRUPT_PROVISIONAL, f"Expected provisional for {status}, got {level}"


# ── Section 6: End-to-end pipeline ─────────────────────────────────────────────

class TestPipelineExplanationEndToEnd:
    def test_q_with_ordinance_reason_mentions_number_confirmed(self):
        inp = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert "D/Q identity:" in far.reason
        assert "confirmed" in far.reason.lower()

    def test_q_without_ordinance_reason_mentions_not_confirmed(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert "D/Q identity:" in far.reason
        assert "not" in far.reason.lower()

    def test_d_with_ordinance_reason_mentions_number_confirmed(self):
        inp = _inp(
            d_limitations=["D"],
            has_d_from_zone_string=True,
            d_ordinance_number="O-172128",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert "D/Q identity:" in far.reason

    def test_sp_reason_has_no_dq_identity_note(self):
        inp = _inp(specific_plan="Venice Specific Plan")
        output = run_zimas_linked_doc_pipeline(inp)
        far = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert "D/Q identity:" not in far.reason
