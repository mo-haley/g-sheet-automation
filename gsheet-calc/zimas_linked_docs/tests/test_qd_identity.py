"""Tests for Q/D ordinance identity and confidence state pass (Pass A).

Verifies that:
1. Bare Q / bare D records remain at detected_not_interpreted
2. Q / D records with a stable ordinance number upgrade to surface_usable
3. Provenance (ordinance number, "content not interpreted" caution) is in extraction_notes
4. Parse-confidence weak / unresolved scenarios handled conservatively
5. Gatekeeper interrupt posture is UNCHANGED by confidence upgrade:
   stronger ordinance identity ≠ weaker interrupt signal
"""

from __future__ import annotations

import pytest

from zimas_linked_docs.models import (
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    FETCH_NEVER,
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_SURFACE_USABLE,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_NONE,
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasLinkedDocInput,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
)
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.gatekeeper import evaluate_interrupts
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_q_record(
    doc_label: str = "Q",
    source_ordinance_number: str | None = None,
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-q-001",
        doc_type=DOC_TYPE_Q_CONDITION,
        doc_label=doc_label,
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        fetch_decision=FETCH_NEVER,
        confidence_state=confidence_state,
        doc_type_notes="Q condition test record.",
        source_ordinance_number=source_ordinance_number,
    )


def _make_d_record(
    doc_label: str = "D",
    source_ordinance_number: str | None = None,
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED,
) -> LinkedDocRecord:
    return LinkedDocRecord(
        record_id="test-d-001",
        doc_type=DOC_TYPE_D_LIMITATION,
        doc_label=doc_label,
        usability_posture=POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
        fetch_decision=FETCH_NEVER,
        confidence_state=confidence_state,
        doc_type_notes="D limitation test record.",
        source_ordinance_number=source_ordinance_number,
    )


def _inp(**kwargs) -> ZimasLinkedDocInput:
    return ZimasLinkedDocInput(apn="1234-567-890", **kwargs)


def _registry_with(*records: LinkedDocRecord) -> LinkedDocRegistry:
    return LinkedDocRegistry(apn="1234-567-890", records=list(records))


# ── Scenario 1: Bare Q / D — no ordinance → detected_not_interpreted ─────────

class TestBareQDNoOrdinance:
    def test_bare_q_stays_detected_not_interpreted(self):
        record = _make_q_record(source_ordinance_number=None)
        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_bare_d_stays_detected_not_interpreted(self):
        record = _make_d_record(source_ordinance_number=None)
        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_DETECTED_NOT_INTERPRETED

    def test_bare_q_extraction_notes_empty(self):
        record = _make_q_record(source_ordinance_number=None)
        assign_confidence_states([record])
        assert record.extraction_notes == ""

    def test_bare_d_extraction_notes_empty(self):
        record = _make_d_record(source_ordinance_number=None)
        assign_confidence_states([record])
        assert record.extraction_notes == ""

    def test_bare_q_usability_posture_unchanged(self):
        record = _make_q_record(source_ordinance_number=None)
        assign_confidence_states([record])
        assert record.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY


# ── Scenario 2: Q with ordinance → surface_usable ────────────────────────────

class TestQWithOrdinance:
    def test_q_with_ordinance_upgrades_to_surface_usable(self):
        record = _make_q_record(source_ordinance_number="186481")
        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_SURFACE_USABLE

    def test_q_ordinance_in_extraction_notes(self):
        record = _make_q_record(source_ordinance_number="186481")
        assign_confidence_states([record])
        assert "186481" in record.extraction_notes

    def test_q_extraction_notes_cautions_content_not_interpreted(self):
        """extraction_notes must explicitly say content was not interpreted."""
        record = _make_q_record(source_ordinance_number="186481")
        assign_confidence_states([record])
        notes = record.extraction_notes.lower()
        assert "not" in notes
        assert any(word in notes for word in ("content", "interpreted", "restriction"))

    def test_q_usability_posture_unchanged_after_upgrade(self):
        """Confidence upgrade must not touch usability_posture."""
        record = _make_q_record(source_ordinance_number="186481")
        assign_confidence_states([record])
        assert record.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY

    def test_q_upgrade_produces_no_issues(self):
        """Successful ordinance identity upgrade is not an error condition."""
        record = _make_q_record(source_ordinance_number="186481")
        _, issues = assign_confidence_states([record])
        # No issues for a clean upgrade; errors are for failed fetches
        assert not any(i.severity == "error" for i in issues)


# ── Scenario 3: D with ordinance → surface_usable ────────────────────────────

class TestDWithOrdinance:
    def test_d_with_ordinance_upgrades_to_surface_usable(self):
        record = _make_d_record(source_ordinance_number="185539")
        records_after, _ = assign_confidence_states([record])
        assert records_after[0].confidence_state == CONF_SURFACE_USABLE

    def test_d_ordinance_in_extraction_notes(self):
        record = _make_d_record(source_ordinance_number="185539")
        assign_confidence_states([record])
        assert "185539" in record.extraction_notes

    def test_d_extraction_notes_cautions_content_not_interpreted(self):
        record = _make_d_record(source_ordinance_number="185539")
        assign_confidence_states([record])
        notes = record.extraction_notes.lower()
        assert any(word in notes for word in ("content", "interpreted", "restriction"))

    def test_d_usability_posture_unchanged_after_upgrade(self):
        record = _make_d_record(source_ordinance_number="185539")
        assign_confidence_states([record])
        assert record.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY


# ── Scenario 4: Parse-confidence weak / unresolved ───────────────────────────
#
# In the normal data flow, zoning_parse_confidence="unresolved" means the zone
# string could not be parsed, so q_ordinance_number / d_ordinance_number will
# be None. The confidence upgrade rule does not fire because source_ordinance_number
# is None — handled correctly without any special-casing in confidence.py.
#
# zoning_parse_confidence="provisional" with an ordinance number present means
# we have a parsed number (potentially imprecise), which is still a stable reference.
# The upgrade fires — provisional parsing is better than no parsing.

class TestParseConfidenceWeakOrUnresolved:
    def test_unresolved_parse_no_ordinance_stays_detected(self):
        """Unresolved parse → ordinance=None → no upgrade (end-to-end pipeline)."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="unresolved",
            has_q_from_zone_string=False,
            q_ordinance_number=None,
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in q_records
        )

    def test_provisional_parse_with_ordinance_upgrades(self):
        """Provisional parse + ordinance present → upgrade fires."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="provisional",
            has_q_from_zone_string=False,
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(r.confidence_state == CONF_SURFACE_USABLE for r in q_records)

    def test_bare_q_from_site_field_no_ordinance_stays_detected(self):
        """Q from Site.q_conditions with no ordinance stays at detected_not_interpreted."""
        inp = _inp(q_conditions=["Q condition language here"])
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in q_records
        )

    def test_d_unresolved_parse_no_ordinance_stays_detected(self):
        inp = _inp(
            d_limitations=["D"],
            zoning_parse_confidence="unresolved",
            has_d_from_zone_string=False,
            d_ordinance_number=None,
        )
        output = run_zimas_linked_doc_pipeline(inp)
        d_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_D_LIMITATION]
        assert d_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in d_records
        )

    def test_d_provisional_parse_with_ordinance_upgrades(self):
        inp = _inp(
            d_limitations=["D"],
            zoning_parse_confidence="provisional",
            has_d_from_zone_string=False,
            d_ordinance_number="185539",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        d_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_D_LIMITATION]
        assert d_records
        assert all(r.confidence_state == CONF_SURFACE_USABLE for r in d_records)


# ── Scenario 6: Hardening — unresolved parse with ordinance present ───────────
#
# The contradiction: caller provides q_ordinance_number / d_ordinance_number
# AND zoning_parse_confidence="unresolved". The ordinance number should NOT
# be forwarded as trusted source_ordinance_number — it came from a failed parse.
# The fix lives in link_detector.py (suppressed before candidate creation).
# confidence.py is unchanged and correct; it never sees the suppressed number.

class TestUnresolvedParseOrdinanceSuppression:
    def test_unresolved_parse_with_q_ordinance_does_not_upgrade(self):
        """Q ordinance present but parse unresolved → source_ordinance_number suppressed → no upgrade."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="unresolved",
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in q_records
        )

    def test_unresolved_parse_with_d_ordinance_does_not_upgrade(self):
        """D ordinance present but parse unresolved → source_ordinance_number suppressed → no upgrade."""
        inp = _inp(
            d_limitations=["D"],
            zoning_parse_confidence="unresolved",
            d_ordinance_number="185539",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        d_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_D_LIMITATION]
        assert d_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in d_records
        )

    def test_unresolved_parse_with_q_ordinance_source_ordinance_number_is_none(self):
        """source_ordinance_number is None on the record — suppressed at detector."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="unresolved",
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(r.source_ordinance_number is None for r in q_records)

    def test_unresolved_parse_without_ordinance_unchanged(self):
        """Unresolved parse with no ordinance: no suppression, behavior unchanged from before."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="unresolved",
            q_ordinance_number=None,
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(
            r.confidence_state == CONF_DETECTED_NOT_INTERPRETED for r in q_records
        )

    def test_suppression_warning_visible_in_issues(self):
        """When ordinance is suppressed, a warning issue surfaces the contradiction."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="unresolved",
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        warning_msgs = [
            i.message for i in output.all_issues
            if i.severity == "warning" and "q_ordinance_number" in i.field.lower()
        ]
        assert warning_msgs, "Expected a warning about suppressed Q ordinance number"
        assert "186481" in warning_msgs[0]
        assert "unresolved" in warning_msgs[0].lower()

    def test_d_suppression_warning_visible_in_issues(self):
        inp = _inp(
            d_limitations=["D"],
            zoning_parse_confidence="unresolved",
            d_ordinance_number="185539",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        warning_msgs = [
            i.message for i in output.all_issues
            if i.severity == "warning" and "d_ordinance_number" in i.field.lower()
        ]
        assert warning_msgs, "Expected a warning about suppressed D ordinance number"
        assert "185539" in warning_msgs[0]

    def test_confirmed_parse_with_ordinance_still_upgrades(self):
        """Hardening must not break the valid confirmed-parse upgrade path."""
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence="confirmed",
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(r.confidence_state == CONF_SURFACE_USABLE for r in q_records)

    def test_no_suppression_when_parse_confidence_is_none(self):
        """No parse result at all (None confidence): ordinance passed through if present.

        Callers that set q_ordinance_number without any zone string parse
        (e.g. from a separate confirmed source) should not be penalised.
        """
        inp = _inp(
            q_conditions=["Q"],
            zoning_parse_confidence=None,
            q_ordinance_number="186481",
        )
        output = run_zimas_linked_doc_pipeline(inp)
        q_records = [r for r in output.registry.records if r.doc_type == DOC_TYPE_Q_CONDITION]
        assert q_records
        assert all(r.confidence_state == CONF_SURFACE_USABLE for r in q_records)


# ── Scenario 5: Gatekeeper parity — interrupt not weakened by identity ────────
#
# This is the critical invariant: stronger ordinance identity does NOT make
# interrupt posture more permissive.
#
# CPIO has a two-level interrupt (UNRESOLVED at detected_not_interpreted,
# PROVISIONAL at surface_usable) because surface_usable CPIO means we have
# confirmed chapter structure. For Q/D, surface_usable means only "we have
# an ordinance number" — content is still unknown. The same reduction would
# be incorrect for Q/D.
#
# gatekeeper.py Q/D rules intentionally do NOT vary by confidence_state.

class TestGatekeeperParityQD:
    def test_q_with_ordinance_still_provisional_on_all_topics(self):
        """Q at surface_usable still produces INTERRUPT_PROVISIONAL on all topics."""
        record = _make_q_record(source_ordinance_number="186481")
        record.confidence_state = CONF_SURFACE_USABLE  # post-upgrade
        decisions, _ = evaluate_interrupts(
            _registry_with(record),
            topics=["FAR", "density", "parking", "setback", "height"],
        )
        levels = {d.topic: d.interrupt_level for d in decisions}
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL
        assert levels["parking"] == INTERRUPT_PROVISIONAL
        assert levels["setback"] == INTERRUPT_PROVISIONAL
        assert levels["height"] == INTERRUPT_PROVISIONAL

    def test_d_with_ordinance_still_provisional_on_density_far(self):
        """D at surface_usable still produces INTERRUPT_PROVISIONAL on density/FAR."""
        record = _make_d_record(source_ordinance_number="185539")
        record.confidence_state = CONF_SURFACE_USABLE
        decisions, _ = evaluate_interrupts(
            _registry_with(record),
            topics=["FAR", "density"],
        )
        levels = {d.topic: d.interrupt_level for d in decisions}
        assert levels["FAR"] == INTERRUPT_PROVISIONAL
        assert levels["density"] == INTERRUPT_PROVISIONAL

    def test_d_with_ordinance_does_not_interrupt_other_topics(self):
        """D limitation scope: parking/setback/height unaffected, regardless of confidence."""
        record = _make_d_record(source_ordinance_number="185539")
        record.confidence_state = CONF_SURFACE_USABLE
        decisions, _ = evaluate_interrupts(
            _registry_with(record),
            topics=["parking", "setback", "height"],
        )
        levels = {d.topic: d.interrupt_level for d in decisions}
        assert levels["parking"] == INTERRUPT_NONE
        assert levels["setback"] == INTERRUPT_NONE
        assert levels["height"] == INTERRUPT_NONE

    def test_bare_q_and_q_with_ordinance_produce_same_interrupt_level(self):
        """Interrupt level is the same whether or not ordinance number is present."""
        bare = _make_q_record(source_ordinance_number=None)
        bare.confidence_state = CONF_DETECTED_NOT_INTERPRETED

        with_ord = _make_q_record(source_ordinance_number="186481")
        with_ord.record_id = "test-q-002"
        with_ord.confidence_state = CONF_SURFACE_USABLE

        decisions_bare, _ = evaluate_interrupts(_registry_with(bare), topics=["FAR"])
        decisions_with, _ = evaluate_interrupts(_registry_with(with_ord), topics=["FAR"])

        assert decisions_bare[0].interrupt_level == decisions_with[0].interrupt_level

    def test_q_with_ordinance_is_not_blocking(self):
        """INTERRUPT_PROVISIONAL is not blocking — only UNRESOLVED/REFUSE are."""
        record = _make_q_record(source_ordinance_number="186481")
        record.confidence_state = CONF_SURFACE_USABLE
        decisions, _ = evaluate_interrupts(_registry_with(record), topics=["FAR"])
        assert not any(d.blocking for d in decisions)

    def test_d_with_ordinance_is_not_blocking(self):
        record = _make_d_record(source_ordinance_number="185539")
        record.confidence_state = CONF_SURFACE_USABLE
        decisions, _ = evaluate_interrupts(_registry_with(record), topics=["FAR", "density"])
        assert not any(d.blocking for d in decisions)
