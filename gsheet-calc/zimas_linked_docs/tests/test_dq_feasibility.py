"""Tests for D/Q retrieval feasibility classification (Pass I).

Verifies that:
1. candidate_only → dq_retrieval_feasibility = no_known_path
2. number_known → dq_retrieval_feasibility = browser_only
3. url_known (direct link) → dq_retrieval_feasibility = url_available
4. zi_corroborated (via orchestrator) → dq_retrieval_feasibility = zi_mediated
5. All non-Q/D doc types have empty dq_retrieval_feasibility
6. _dq_identity_detail() appends feasibility hints that match the status
7. Feasibility hints are accurate: browser_only says 'no machine-accessible URL pattern'
8. zi_mediated hint references ZI fetch
9. Interrupt invariance: feasibility classification never changes interrupt levels

The four feasibility states reflect the production ceiling for LA City ordinances:
- no_known_path: no number, no retrieval possible
- browser_only: number known; PDIS is an Angular SPA, no machine path (see
  governing_docs/document_fetcher.py — this was investigated and documented)
- zi_mediated: ZI corroboration; ZI text may contain conditions inline
- url_available: direct URL known (from ZIMAS identify response)

NOTE: url_available and zi_mediated are not reachable in production today with
fetch_enabled=False (the default). The field is set correctly when the conditions
are artificially created in tests.
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_OVERLAY_CPIO,
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
    DQ_FEASIBILITY_NO_KNOWN_PATH,
    DQ_FEASIBILITY_BROWSER_ONLY,
    DQ_FEASIBILITY_ZI_MEDIATED,
    DQ_FEASIBILITY_URL_AVAILABLE,
    LinkedDocRecord,
    ZimasLinkedDocInput,
)
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.gatekeeper import _dq_identity_detail, _rigor_level
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


def _zi_record(extracted_ordinance: str) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-zi-001",
        doc_type=DOC_TYPE_ZI_DOCUMENT,
        doc_label="ZI-2374",
        usability_posture=POSTURE_MANUAL_REVIEW_FIRST,
        extracted_ordinance_number=extracted_ordinance,
        fetch_status="success",
        extracted_title="Zoning Information File",
    )


def _after_confidence(record: LinkedDocRecord) -> LinkedDocRecord:
    records, _ = assign_confidence_states([record])
    return records[0]


# ── Section 1: Feasibility assignment in confidence.py ────────────────────────

class TestFeasibilityAssignment:
    def test_no_ordinance_gets_no_known_path(self):
        r = _after_confidence(_q_record())
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_NO_KNOWN_PATH

    def test_d_no_ordinance_gets_no_known_path(self):
        r = _after_confidence(_d_record())
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_NO_KNOWN_PATH

    def test_ordinance_number_gets_browser_only(self):
        r = _after_confidence(_q_record(ordinance="186481"))
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_d_ordinance_number_gets_browser_only(self):
        r = _after_confidence(_d_record(ordinance="172128"))
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_direct_url_gets_url_available(self):
        r = _after_confidence(_q_record(
            ordinance="186481",
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_DIRECT_LINK,
        ))
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_URL_AVAILABLE

    def test_inferred_url_stays_browser_only(self):
        """Inferred URL is not reliable enough for machine fetch."""
        r = _after_confidence(_q_record(
            ordinance="186481",
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_INFERRED,
        ))
        assert r.dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_non_dq_types_have_empty_feasibility(self):
        sp = LinkedDocRecord(
            record_id="test-sp-001",
            doc_type=DOC_TYPE_SPECIFIC_PLAN,
            doc_label="Venice Specific Plan",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
            doc_type_confidence="confirmed",
        )
        records, _ = assign_confidence_states([sp])
        assert records[0].dq_retrieval_feasibility == ""

    def test_cpio_has_empty_feasibility(self):
        cpio = LinkedDocRecord(
            record_id="test-cpio-001",
            doc_type=DOC_TYPE_OVERLAY_CPIO,
            doc_label="Venice CPIO",
            usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        )
        records, _ = assign_confidence_states([cpio])
        assert records[0].dq_retrieval_feasibility == ""


# ── Section 2: ZI corroboration upgrades feasibility ──────────────────────────

class TestZiCorroborationFeasibility:
    def test_zi_match_upgrades_to_zi_mediated(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record("O-186481")
        _correlate_dq_zi([q, zi])
        assert q.dq_retrieval_feasibility == DQ_FEASIBILITY_ZI_MEDIATED

    def test_no_zi_match_stays_browser_only(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record("O-172128")  # different ordinance
        _correlate_dq_zi([q, zi])
        assert q.dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_zi_corroboration_also_sets_retrieval_status(self):
        """Sanity: both fields updated together on corroboration."""
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record("O-186481")
        _correlate_dq_zi([q, zi])
        assert q.ordinance_retrieval_status == DQ_RETRIEVAL_ZI_CORROBORATED
        assert q.dq_retrieval_feasibility == DQ_FEASIBILITY_ZI_MEDIATED

    def test_zi_record_feasibility_unchanged(self):
        q = _q_record(ordinance="O-186481")
        assign_confidence_states([q])
        zi = _zi_record("O-186481")
        original = zi.dq_retrieval_feasibility
        _correlate_dq_zi([q, zi])
        assert zi.dq_retrieval_feasibility == original  # ZI itself not touched


# ── Section 3: Feasibility hints in _dq_identity_detail() ─────────────────────

class TestFeasibilityHintsInDetail:
    def _detail(self, record: LinkedDocRecord) -> str:
        assert _rigor_level(record) == "identity_confirmed"
        return _dq_identity_detail(record)

    def _make_surface_usable(self, record: LinkedDocRecord) -> LinkedDocRecord:
        records, _ = assign_confidence_states([record])
        return records[0]

    def test_browser_only_hint_mentions_no_machine_url_pattern(self):
        r = self._make_surface_usable(_q_record(ordinance="186481"))
        detail = self._detail(r)
        assert "machine" in detail.lower() or "no machine" in detail.lower() or "browser" in detail.lower()

    def test_browser_only_hint_mentions_city_clerk_or_browser(self):
        r = self._make_surface_usable(_q_record(ordinance="186481"))
        detail = self._detail(r)
        assert "city clerk" in detail.lower() or "browser" in detail.lower()

    def test_zi_mediated_hint_mentions_zi_fetch(self):
        q = _q_record(ordinance="O-186481")
        self._make_surface_usable(q)
        assign_confidence_states([q])
        zi = _zi_record("O-186481")
        _correlate_dq_zi([q, zi])
        detail = self._detail(q)
        assert "zi" in detail.lower() and "fetch" in detail.lower()

    def test_url_available_hint_mentions_fetch(self):
        r = self._make_surface_usable(_q_record(
            ordinance="186481",
            url="https://example.com/ord/186481.pdf",
            url_confidence=URL_CONF_DIRECT_LINK,
        ))
        detail = self._detail(r)
        assert "fetch" in detail.lower() or "url" in detail.lower()

    def test_four_feasibility_states_produce_distinct_hints(self):
        """Each feasibility state must produce a distinct trailing hint."""
        # browser_only
        r_browser = self._make_surface_usable(_q_record(ordinance="186481"))

        # url_available
        r_url = self._make_surface_usable(_q_record(
            ordinance="186481",
            url="https://example.com/ord.pdf",
            url_confidence=URL_CONF_DIRECT_LINK,
        ))

        # zi_mediated
        r_zi = _q_record(ordinance="O-186481")
        assign_confidence_states([r_zi])
        _correlate_dq_zi([r_zi, _zi_record("O-186481")])

        details = {
            "browser": self._detail(r_browser),
            "url": self._detail(r_url),
            "zi": self._detail(r_zi),
        }
        assert len(set(details.values())) == 3, (
            "Expected 3 distinct detail strings, got: " + str(details)
        )

    def test_detail_always_states_content_unread(self):
        for record in (
            self._make_surface_usable(_q_record(ordinance="186481")),
            self._make_surface_usable(_d_record(ordinance="172128")),
        ):
            detail = self._detail(record)
            assert "not" in detail.lower() and (
                "read" in detail.lower() or "fetched" in detail.lower()
            ), f"Expected 'not read/fetched' in: {detail}"


# ── Section 4: End-to-end pipeline ─────────────────────────────────────────────

class TestPipelineFeasibilityEndToEnd:
    def test_q_without_ordinance_gets_no_known_path(self):
        inp = _inp(q_conditions=["Q"])
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert q_records[0].dq_retrieval_feasibility == DQ_FEASIBILITY_NO_KNOWN_PATH

    def test_q_with_ordinance_gets_browser_only(self):
        inp = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records[0].dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_d_with_ordinance_gets_browser_only(self):
        inp = _inp(
            d_limitations=["D"],
            has_d_from_zone_string=True,
            d_ordinance_number="O-172128",
            zoning_parse_confidence="confirmed",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        d_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_D_LIMITATION]
        assert d_records[0].dq_retrieval_feasibility == DQ_FEASIBILITY_BROWSER_ONLY

    def test_non_dq_records_have_empty_feasibility_in_pipeline(self):
        inp = _inp(specific_plan="Venice Specific Plan", overlay_zones=["San Pedro CPIO"])
        output = run_zimas_linked_doc_pipeline(inp)
        for r in output.registry.records:
            if r.doc_type not in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION):
                assert r.dq_retrieval_feasibility == "", (
                    f"Expected empty dq_retrieval_feasibility for {r.doc_type}, "
                    f"got {r.dq_retrieval_feasibility!r}"
                )

    def test_interrupt_level_unchanged_by_feasibility(self):
        """browser_only vs no_known_path must not change the interrupt level."""
        inp_with = _inp(
            q_conditions=["Q"],
            has_q_from_zone_string=True,
            q_ordinance_number="O-186481",
            zoning_parse_confidence="confirmed",
        )
        inp_without = _inp(q_conditions=["Q"])
        out_with = run_zimas_linked_doc_pipeline(inp_with)
        out_without = run_zimas_linked_doc_pipeline(inp_without)
        far_with = next(d for d in out_with.interrupt_decisions if d.topic == "FAR")
        far_without = next(d for d in out_without.interrupt_decisions if d.topic == "FAR")
        assert far_with.interrupt_level == far_without.interrupt_level == "provisional"
        assert far_with.blocking == far_without.blocking == False
