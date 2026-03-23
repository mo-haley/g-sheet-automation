"""Tests for D/Q document-backed identity and retrieval feasibility (Pass G).

Verifies that:
1. A Q/D record with no ordinance number gets ordinance_retrieval_status=candidate_only
2. A Q/D record with a confirmed ordinance number gets number_known
3. A Q/D record with a confirmed ordinance number AND a direct-link URL gets url_known
4. ordinance_retrieval_status is empty string for all non-Q/D doc types
5. ZI corroboration: when a fetched ZI record's extracted_ordinance_number matches a D/Q
   record's source_ordinance_number, the status upgrades to zi_corroborated
6. ZI corroboration is case/whitespace-insensitive (normalised comparison)
7. No false corroboration when ordinance numbers differ
8. Interrupt levels are INVARIANT — retrieval status changes never soften interrupts
9. Multiple Q/D records: only the matching one is corroborated
10. No regression on SP, CPIO, case document handling

NOTE: ordinance_retrieval_status describes document retrieval feasibility only.
It does NOT change confidence_state ordering beyond what is already set by
confidence.py, and it NEVER changes interrupt levels or blocking posture.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_UNRESOLVED,
    CONF_SURFACE_USABLE,
    CONF_DETECTED_NOT_INTERPRETED,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    POSTURE_MANUAL_REVIEW_FIRST,
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
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline, _correlate_dq_zi


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _q_record(
    ordinance: str | None = None,
    url: str | None = None,
    url_confidence: str = URL_CONF_INFERRED,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-q-001",
        doc_type=DOC_TYPE_Q_CONDITION,
        doc_label="Q" if not ordinance else f"Q (O-{ordinance})",
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        source_ordinance_number=ordinance,
        url=url,
        url_confidence=url_confidence,
    )


def _d_record(ordinance: str | None = None) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-d-001",
        doc_type=DOC_TYPE_D_LIMITATION,
        doc_label="D" if not ordinance else f"D (O-{ordinance})",
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        source_ordinance_number=ordinance,
    )


def _zi_record(extracted_ordinance: str | None = None) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-zi-001",
        doc_type=DOC_TYPE_ZI_DOCUMENT,
        doc_label="ZI-2374",
        usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
        extracted_ordinance_number=extracted_ordinance,
        fetch_status="success",
        extracted_title="Zoning Information File",
    )


def _run_confidence(record: LinkedDocRecord) -> LinkedDocRecord:
    records, _ = assign_confidence_states([record])
    return records[0]


# ── Section 1: Retrieval feasibility classification ────────────────────────────

class TestRetrievalStatusAssignment:
    def test_q_without_ordinance_gets_candidate_only(self):
        r = _run_confidence(_q_record())
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_CANDIDATE_ONLY

    def test_d_without_ordinance_gets_candidate_only(self):
        r = _run_confidence(_d_record())
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_CANDIDATE_ONLY

    def test_q_with_ordinance_gets_number_known(self):
        r = _run_confidence(_q_record(ordinance="186481"))
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_d_with_ordinance_gets_number_known(self):
        r = _run_confidence(_d_record(ordinance="172128"))
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_q_with_ordinance_and_direct_url_gets_url_known(self):
        r = _run_confidence(_q_record(
            ordinance="186481",
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_DIRECT_LINK,
        ))
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_URL_KNOWN

    def test_q_with_ordinance_and_inferred_url_stays_number_known(self):
        """Inferred/portal URLs don't earn url_known — direct link required."""
        r = _run_confidence(_q_record(
            ordinance="186481",
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_INFERRED,
        ))
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_q_url_without_ordinance_stays_candidate_only(self):
        """URL alone does not supply an ordinance number."""
        r = _run_confidence(_q_record(
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_DIRECT_LINK,
        ))
        assert r.ordinance_retrieval_status == DQ_RETRIEVAL_CANDIDATE_ONLY


# ── Section 2: Confidence state is independent of retrieval status ─────────────

class TestConfidenceStateUnchanged:
    def test_q_with_ordinance_still_gets_surface_usable(self):
        r = _run_confidence(_q_record(ordinance="186481"))
        assert r.confidence_state == CONF_SURFACE_USABLE

    def test_q_without_ordinance_stays_detected_not_interpreted(self):
        r = _run_confidence(_q_record())
        assert r.confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_retrieval_status_is_empty_for_non_dq_types(self):
        """Non-Q/D records must never have ordinance_retrieval_status set."""
        sp_record = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            doc_type_confidence="confirmed",
        )
        after, _ = assign_confidence_states([sp_record])
        assert after[0].ordinance_retrieval_status == ""

    def test_retrieval_status_is_empty_for_cpio(self):
        cpio_record = LinkedDocRecord(
            record_id="test-cpio-001",
            doc_type=DOC_TYPE_OVERLAY_CPIO,
            doc_label="Venice CPIO",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        )
        after, _ = assign_confidence_states([cpio_record])
        assert after[0].ordinance_retrieval_status == ""


# ── Section 3: ZI corroboration ────────────────────────────────────────────────

class TestZiCorroboration:
    def test_matching_ordinance_upgrades_to_zi_corroborated(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="O-186481")
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_ZI_CORROBORATED

    def test_corroboration_is_case_insensitive(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="o-186481")
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_ZI_CORROBORATED

    def test_corroboration_strips_whitespace(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="  O-186481  ")
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_ZI_CORROBORATED

    def test_no_corroboration_when_numbers_differ(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="O-172128")
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_no_corroboration_when_zi_has_no_extracted_ordinance(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance=None)
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_no_corroboration_when_dq_has_no_ordinance(self):
        q = _q_record(ordinance=None)
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="O-186481")
        _correlate_dq_zi([q, zi])
        # q has no source_ordinance_number → cannot match → stays candidate_only
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_CANDIDATE_ONLY

    def test_corroboration_only_affects_matching_record(self):
        """When two Q records exist, only the matching one gets corroborated."""
        q1 = _q_record(ordinance="O-186481")
        q1.record_id = "test-q-001"
        q2 = _q_record(ordinance="O-172128")
        q2.record_id = "test-q-002"
        assign_confidence_states([q1, q2])
        zi = _zi_record(extracted_ordinance="O-186481")
        _correlate_dq_zi([q1, q2, zi])
        assert q1.ordinance_retrieval_status == DQ_RETRIEVAL_ZI_CORROBORATED
        assert q2.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_zi_record_itself_is_never_modified(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record(extracted_ordinance="O-186481")
        original_zi_status = zi.ordinance_retrieval_status
        _correlate_dq_zi([q, zi])
        assert zi.ordinance_retrieval_status == original_zi_status

    def test_no_zi_records_is_a_noop(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        _correlate_dq_zi([q])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN


# ── Section 4: Interrupt invariance ────────────────────────────────────────────

class TestInterruptInvariance:
    """Retrieval status improvements must never change interrupt levels."""

    def test_q_candidate_only_still_interrupts_provisionally(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL
        assert not far_decision.blocking

    def test_q_with_ordinance_still_interrupts_provisionally(self):
        inp = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL
        assert not far_decision.blocking

    def test_d_with_ordinance_still_interrupts_provisionally(self):
        inp = _inp(
            d_limitations=["D"],
            has_d_from_zone_string=True,
            d_ordinance_number="O-172128",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_PROVISIONAL
        assert not far_decision.blocking

    def test_sp_interrupt_still_unresolved_with_q_present(self):
        """Specific plan remains UNRESOLVED regardless of Q retrieval status."""
        inp = _inp(
            specific_plan="Venice Specific Plan",
            q_conditions=["Q"],
        )
        output = run_zimas_linked_doc_pipeline(inp)
        far_decision = next(d for d in output.interrupt_decisions if d.topic == "FAR")
        assert far_decision.interrupt_level == INTERRUPT_UNRESOLVED
        assert far_decision.blocking


# ── Section 5: End-to-end pipeline ─────────────────────────────────────────────

class TestPipelineEndToEnd:
    def test_q_without_ordinance_produces_candidate_only_in_pipeline(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert q_records[0].ordinance_retrieval_status == DQ_RETRIEVAL_CANDIDATE_ONLY

    def test_q_with_ordinance_produces_number_known_in_pipeline(self):
        inp = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert q_records[0].ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_d_with_ordinance_produces_number_known_in_pipeline(self):
        inp = _inp(
            d_limitations=["D"],
            has_d_from_zone_string=True,
            d_ordinance_number="O-172128",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        d_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_D_LIMITATION]
        assert d_records
        assert d_records[0].ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN

    def test_non_dq_records_have_empty_retrieval_status_in_pipeline(self):
        inp = _inp(specific_plan="Venice Specific Plan", overlay_zones=["San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        for r in output.registry.records:
            if r.doc_type not in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION):
                assert r.ordinance_retrieval_status == "", (
                    f"Expected empty ordinance_retrieval_status for {r.doc_type}, "
                    f"got {r.ordinance_retrieval_status!r}"
                )

    def test_q_confidence_upgrade_and_retrieval_status_coexist(self):
        """Q with ordinance: confidence_state=surface_usable AND retrieval_status=number_known."""
        inp = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records[0].confidence_state == CONF_SURFACE_USABLE
        assert q_records[0].ordinance_retrieval_status == DQ_RETRIEVAL_NUMBER_KNOWN
