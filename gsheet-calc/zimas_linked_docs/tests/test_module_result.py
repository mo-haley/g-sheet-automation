"""Tests for run_zimas_linked_doc_module — ModuleResult adapter behavior.

Covers the mapping from ZimasLinkedDocOutput to ModuleResult for:
    coverage_level  (complete, partial, thin, uncertain)
    run_status      (ok, partial)
    confidence      (high, medium, low, unresolved)
    blocking        (true / false)
    action_posture  (all five postures exercised)
    module_payload  (preservation of original output)

All tests use run_zimas_linked_doc_module end-to-end.
The internal pipeline is not re-tested here — see test_registry_trust.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zimas_linked_docs.models import ZimasLinkedDocInput
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_module
from models.result_common import (
    RunStatus,
    CoverageLevel,
    ConfidenceLevel,
    ActionPosture,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _fake_identify_with_results() -> dict:
    """Minimal raw_zimas_identify with one result entry (no linked authority)."""
    return {
        "results": [
            {
                "layerId": 103,
                "layerName": "Parcel",
                "attributes": {"AIN": "1234-567-890", "LOT_SQ_FT": "5000"},
            }
        ]
    }


# ── Test 1: complete coverage, clean registry ─────────────────────────────────

class TestCompleteCoverageClean:
    """Raw identify present, confirmed parse, no linked authority.

    Expected mapping:
        run_status      = OK
        coverage_level  = COMPLETE
        confidence      = HIGH
        blocking        = False
        action_posture  = CAN_RELY_WITH_REVIEW
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn="1234-567-890",
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify=_fake_identify_with_results(),
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_run_status_ok(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.OK

    def test_coverage_level_complete(self):
        assert run_zimas_linked_doc_module(self._inp()).coverage_level == CoverageLevel.COMPLETE

    def test_confidence_high(self):
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.HIGH

    def test_not_blocking(self):
        assert run_zimas_linked_doc_module(self._inp()).blocking is False

    def test_action_posture_can_rely(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.CAN_RELY_WITH_REVIEW


# ── Test 2: partial coverage, clean registry ──────────────────────────────────

class TestPartialCoverageClean:
    """No raw identify, confirmed parse, no linked authority.

    Coverage degrades to partial because raw identify is absent.
    Registry is clean (nothing detected from structured fields either).

    Expected mapping:
        run_status      = PARTIAL
        coverage_level  = PARTIAL
        confidence      = MEDIUM  (clean registry, non-complete coverage)
        blocking        = False
        action_posture  = ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn="9999-001-002",
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_run_status_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.PARTIAL

    def test_coverage_level_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).coverage_level == CoverageLevel.PARTIAL

    def test_confidence_medium(self):
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.MEDIUM

    def test_not_blocking(self):
        assert run_zimas_linked_doc_module(self._inp()).blocking is False

    def test_action_posture_review_for_gaps(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS


# ── Test 3: thin coverage ─────────────────────────────────────────────────────

class TestThinCoverage:
    """No raw identify, no structured fields, no zone parse result.

    Thin is produced when raw identify is absent AND zone-string signals are
    also unavailable (parse absent + no structured fields). This is the
    weakest search state short of a parse failure.

    Expected mapping:
        run_status      = PARTIAL
        coverage_level  = THIN
        confidence      = MEDIUM  (clean registry — nothing found — non-complete)
        blocking        = False
        action_posture  = INSUFFICIENT_FOR_PERMIT_USE
                          (thin coverage takes priority over non-blocking state)
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn=None,
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence=None,   # absent — key driver of thin
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_run_status_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.PARTIAL

    def test_coverage_level_thin(self):
        assert run_zimas_linked_doc_module(self._inp()).coverage_level == CoverageLevel.THIN

    def test_confidence_medium(self):
        # THIN is not UNCERTAIN — it degrades search quality, not parse trust.
        # Clean registry + non-complete coverage → MEDIUM, not UNRESOLVED.
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.MEDIUM

    def test_not_blocking(self):
        assert run_zimas_linked_doc_module(self._inp()).blocking is False

    def test_action_posture_insufficient(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.INSUFFICIENT_FOR_PERMIT_USE


# ── Test 4: uncertain coverage ────────────────────────────────────────────────

class TestUncertainCoverage:
    """Zone parse explicitly failed. No raw identify.

    Uncertain coverage means the zone string parse returned 'unresolved' —
    the parse itself is broken, not just absent. This poisons the registry's
    trustworthiness regardless of what was found.

    Expected mapping:
        run_status      = PARTIAL
        coverage_level  = UNCERTAIN
        confidence      = UNRESOLVED  (uncertain coverage → cannot trust registry)
        blocking        = False       (no records found → no interrupt triggers)
        action_posture  = INSUFFICIENT_FOR_PERMIT_USE
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn=None,
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence="unresolved",
            zoning_parse_issues=["Unrecognized base zone in '[LF1'"],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_run_status_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.PARTIAL

    def test_coverage_level_uncertain(self):
        assert run_zimas_linked_doc_module(self._inp()).coverage_level == CoverageLevel.UNCERTAIN

    def test_confidence_unresolved(self):
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.UNRESOLVED

    def test_not_blocking(self):
        # No linked authority was detected, so no blocking interrupt fires.
        # uncertain coverage degrades confidence but does not itself set blocking.
        assert run_zimas_linked_doc_module(self._inp()).blocking is False

    def test_action_posture_insufficient(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.INSUFFICIENT_FOR_PERMIT_USE

    def test_thin_and_uncertain_differ(self):
        """Uncertain confidence must be UNRESOLVED; thin must be MEDIUM.

        These are distinct states. Conflating them would mask parse failures
        behind the weaker 'search was incomplete' signal.
        """
        uncertain_result = run_zimas_linked_doc_module(self._inp())

        thin_inp = ZimasLinkedDocInput(
            apn=None,
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence=None,
        )
        thin_result = run_zimas_linked_doc_module(thin_inp)

        assert uncertain_result.confidence == ConfidenceLevel.UNRESOLVED
        assert thin_result.confidence == ConfidenceLevel.MEDIUM
        assert uncertain_result.coverage_level == CoverageLevel.UNCERTAIN
        assert thin_result.coverage_level == CoverageLevel.THIN


# ── Test 5: specific plan → blocking interrupt ────────────────────────────────

class TestSpecificPlanBlocking:
    """Specific plan detected with partial coverage (raw identify absent).

    Specific plans produce INTERRUPT_UNRESOLVED for all calc topics.
    The registry is 'has_interrupters'. Blocking interrupt takes posture priority.

    Expected mapping:
        run_status      = PARTIAL
        coverage_level  = PARTIAL
        confidence      = LOW     (has_interrupters registry)
        blocking        = True
        action_posture  = AUTHORITY_CONFIRMATION_REQUIRED
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn="2222-333-444",
            specific_plan="Venice Specific Plan",
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_run_status_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.PARTIAL

    def test_coverage_level_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).coverage_level == CoverageLevel.PARTIAL

    def test_confidence_low(self):
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.LOW

    def test_blocking_true(self):
        assert run_zimas_linked_doc_module(self._inp()).blocking is True

    def test_action_posture_authority_confirmation(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED

    def test_authority_posture_beats_partial_coverage(self):
        """Blocking interrupt must take posture priority over partial coverage.

        Without this priority, partial coverage alone would produce
        ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS — masking the blocking
        authority signal behind a softer posture.
        """
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture != ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS


# ── Test 6: Q condition → provisional interrupt, non-blocking ─────────────────

class TestQConditionProvisional:
    """Q condition detected. Partial coverage (no raw identify).

    Q conditions produce INTERRUPT_PROVISIONAL, not blocking. Registry is
    'provisional'. Action posture should reflect detected items, not block.

    Expected mapping:
        run_status      = PARTIAL
        coverage_level  = PARTIAL
        confidence      = MEDIUM  (provisional registry)
        blocking        = False
        action_posture  = ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn="3333-444-555",
            specific_plan=None,
            overlay_zones=[],
            q_conditions=["Q"],
            d_limitations=[],
            raw_zimas_identify={},
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=True,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_not_blocking(self):
        assert run_zimas_linked_doc_module(self._inp()).blocking is False

    def test_confidence_medium(self):
        assert run_zimas_linked_doc_module(self._inp()).confidence == ConfidenceLevel.MEDIUM

    def test_action_posture_review_for_gaps(self):
        result = run_zimas_linked_doc_module(self._inp())
        assert result.interpretation.action_posture == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS

    def test_run_status_partial(self):
        assert run_zimas_linked_doc_module(self._inp()).run_status == RunStatus.PARTIAL


# ── Test 7: module_payload preservation ──────────────────────────────────────

class TestModulePayloadPreservation:
    """module_payload must faithfully serialize the ZimasLinkedDocOutput.

    Uses a specific plan + complete inputs so the payload has interesting content:
    real interrupt decisions, records, and a non-trivial registry.
    """

    def _inp(self) -> ZimasLinkedDocInput:
        return ZimasLinkedDocInput(
            apn="4444-555-666",
            specific_plan="Playa Vista Specific Plan",
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify=_fake_identify_with_results(),
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_payload_has_expected_top_level_keys(self):
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        for key in (
            "registry",
            "interrupt_decisions",
            "all_issues",
            "registry_input_coverage",
            "records_classified",
            "candidates_detected",
            "interpretation",
        ):
            assert key in payload, f"module_payload missing key: {key!r}"

    def test_payload_registry_input_coverage_matches_coverage_level(self):
        """coverage_level in the envelope must match the raw string in the payload."""
        result = run_zimas_linked_doc_module(self._inp())
        assert result.module_payload["registry_input_coverage"] == result.coverage_level.value

    def test_payload_records_classified_is_integer(self):
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        assert isinstance(payload["records_classified"], int)

    def test_payload_interrupt_decisions_is_list(self):
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        assert isinstance(payload["interrupt_decisions"], list)

    def test_payload_interrupt_decisions_nonempty_when_sp_detected(self):
        """Specific plan → all topics interrupted; payload should reflect this."""
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        assert len(payload["interrupt_decisions"]) > 0

    def test_payload_records_classified_positive_when_sp_detected(self):
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        assert payload["records_classified"] >= 1

    def test_payload_all_issues_is_list(self):
        payload = run_zimas_linked_doc_module(self._inp()).module_payload
        assert isinstance(payload["all_issues"], list)


# ── Test 8: ModuleResult schema and fixed fields ──────────────────────────────

class TestModuleResultSchema:
    """Verify fixed metadata and interpretation structure on any valid result."""

    def _run(self):
        return run_zimas_linked_doc_module(
            ZimasLinkedDocInput(
                apn="5555-666-777",
                specific_plan=None,
                overlay_zones=[],
                q_conditions=[],
                d_limitations=[],
                raw_zimas_identify=_fake_identify_with_results(),
                zoning_parse_confidence="confirmed",
            )
        )

    def test_module_name(self):
        assert self._run().module == "zimas_linked_docs"

    def test_module_version(self):
        assert self._run().module_version == "v1"

    def test_interpretation_fields_nonempty(self):
        result = self._run()
        assert result.interpretation.summary, "summary must not be empty"
        assert result.interpretation.plain_language_result, "plain_language_result must not be empty"

    def test_interpretation_summary_contains_coverage(self):
        assert "coverage=" in self._run().interpretation.summary

    def test_interpretation_plain_language_from_pipeline(self):
        """plain_language_result must come from the pipeline's own interpretation,
        not be a synthetic string. Verify it contains pipeline-specific language."""
        result = self._run()
        plain = result.interpretation.plain_language_result
        # The pipeline interpretation always mentions coverage or records.
        assert any(word in plain.lower() for word in ("coverage", "detected", "linked authority")), (
            f"plain_language_result does not look like pipeline interpretation: {plain!r}"
        )

    def test_inputs_summary_has_apn(self):
        assert "apn" in self._run().inputs_summary

    def test_inputs_summary_has_topics_evaluated(self):
        assert "topics_evaluated" in self._run().inputs_summary

    def test_provenance_source_types_populated(self):
        result = self._run()
        # raw identify + confirmed parse → at least two source types
        assert len(result.provenance.source_types) >= 2

    def test_module_result_is_valid_pydantic(self):
        """model_dump() must round-trip without errors."""
        result = self._run()
        dumped = result.model_dump()
        assert dumped["module"] == "zimas_linked_docs"
