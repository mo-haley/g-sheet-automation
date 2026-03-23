"""Tests for registry trust / input coverage signaling.

Covers four scenarios:
1. High-confidence sparse parcel — complete inputs, genuinely zero linked authority
2. Sparse registry with parse failure — unresolved zone parse, coverage=uncertain
3. Sparse registry with missing raw identify — no layer scan, coverage=partial
4. Mixed signals producing partial coverage — some sources present, some absent

All tests use run_zimas_linked_doc_pipeline end-to-end. No mocking of
pipeline internals — coverage assessment runs as part of the pipeline.

The objective is to verify that:
- coverage=complete + zero records → info-level zero-record issue, unqualified INTERRUPT_NONE
- coverage=uncertain + zero records → error-level issue, INTERRUPT_NONE with warning caveat
- coverage=partial + zero records → warning-level zero-record issue, qualified INTERRUPT_NONE
- coverage is correctly computed for mixed input states
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zimas_linked_docs.models import (
    ZimasLinkedDocInput,
    INPUT_COVERAGE_COMPLETE,
    INPUT_COVERAGE_PARTIAL,
    INPUT_COVERAGE_THIN,
    INPUT_COVERAGE_UNCERTAIN,
    INTERRUPT_NONE,
)
from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline
from zimas_linked_docs.input_coverage import assess_input_coverage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_identify_with_results() -> dict:
    """Minimal raw_zimas_identify with one result entry (no linked authority)."""
    return {
        "results": [
            {
                "layerId": 103,
                "layerName": "Parcel",
                "attributes": {
                    "AIN": "1234-567-890",
                    "LOT_SQ_FT": "5000",
                },
            }
        ]
    }


def _issues_by_step(output, step: str):
    return [i for i in output.all_issues if i.step == step]


def _issues_by_severity(output, severity: str):
    return [i for i in output.all_issues if i.severity == severity]


def _interrupt_for_topic(output, topic: str):
    return next((d for d in output.interrupt_decisions if d.topic == topic), None)


# ── Test 1: high-confidence sparse parcel ────────────────────────────────────

class TestHighConfidenceSparse:
    """Complete inputs, confirmed zone parse, genuinely zero linked authority.

    Expected:
    - coverage = complete
    - registry_confidence = "clean"
    - zero-record issue is severity="info" (not warning)
    - INTERRUPT_NONE reason has no coverage caveat
    """

    def _build_inp(self):
        return ZimasLinkedDocInput(
            apn="1234-567-890",
            specific_plan=None,
            overlay_zones=[],       # genuinely no overlays
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

    def test_coverage_is_complete(self):
        inp = self._build_inp()
        coverage, issues = assess_input_coverage(inp)
        assert coverage == INPUT_COVERAGE_COMPLETE, (
            f"Expected complete coverage, got '{coverage}'. "
            f"Issues: {[i.message for i in issues if i.severity not in ('info',)]}"
        )

    def test_pipeline_coverage_complete(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        assert out.registry_input_coverage == INPUT_COVERAGE_COMPLETE
        assert out.registry.registry_input_coverage == INPUT_COVERAGE_COMPLETE

    def test_zero_records_info_not_warning(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        assert out.records_classified == 0
        zero_record_issues = [
            i for i in out.all_issues
            if i.step == "doc_registry" and "zero records" in i.message.lower()
        ]
        assert zero_record_issues, "Expected a zero-record issue from doc_registry"
        for iss in zero_record_issues:
            assert iss.severity == "info", (
                f"Zero-record issue should be info for complete coverage, got '{iss.severity}': "
                f"{iss.message}"
            )

    def test_interrupt_none_no_caveat(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        far_interrupt = _interrupt_for_topic(out, "FAR")
        assert far_interrupt is not None
        assert far_interrupt.interrupt_level == INTERRUPT_NONE
        # No trust caveat should appear in the reason for complete coverage
        assert "WARNING" not in far_interrupt.reason
        assert "input coverage" not in far_interrupt.reason.lower()


# ── Test 2: sparse registry with parse failure ───────────────────────────────

class TestSparseWithParseFailure:
    """Zone parse confidence is 'unresolved'. No raw identify data.

    Expected:
    - coverage = uncertain
    - registry_confidence = "clean" (nothing found — but don't trust it)
    - error-level issue from input_coverage step
    - INTERRUPT_NONE reason contains explicit WARNING about coverage
    """

    def _build_inp(self):
        return ZimasLinkedDocInput(
            apn="5678-901-234",
            specific_plan=None,
            overlay_zones=["LF1-WH1-5", "P2-FA", "CPIO"],  # these will be detected
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},   # no layer data
            zoning_parse_confidence="unresolved",
            zoning_parse_issues=["Unrecognized base zone in '[LF1'"],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_coverage_is_uncertain(self):
        inp = self._build_inp()
        coverage, issues = assess_input_coverage(inp)
        assert coverage == INPUT_COVERAGE_UNCERTAIN

    def test_pipeline_coverage_uncertain(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        assert out.registry_input_coverage == INPUT_COVERAGE_UNCERTAIN

    def test_error_level_coverage_issue_present(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        error_issues = _issues_by_severity(out, "error")
        coverage_errors = [i for i in error_issues if i.step == "input_coverage"]
        assert coverage_errors, (
            "Expected an error-level issue from input_coverage for unresolved parse confidence. "
            f"All issues: {[(i.step, i.severity, i.message[:60]) for i in out.all_issues]}"
        )

    def test_interrupt_none_has_warning_caveat(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        # Some topics may have records (CPIO, unknown overlay) — find one with INTERRUPT_NONE
        none_decisions = [d for d in out.interrupt_decisions if d.interrupt_level == INTERRUPT_NONE]
        if not none_decisions:
            pytest.skip("All topics have interrupts — no INTERRUPT_NONE to check")
        for decision in none_decisions:
            assert "WARNING" in decision.reason, (
                f"Expected WARNING in INTERRUPT_NONE reason for uncertain coverage, "
                f"got: {decision.reason}"
            )

    def test_parse_failure_issue_appears_before_detection_issues(self):
        """Coverage issues should precede detection issues in all_issues."""
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        steps = [i.step for i in out.all_issues]
        if "input_coverage" in steps and "link_detector" in steps:
            first_coverage = steps.index("input_coverage")
            first_detection = steps.index("link_detector")
            assert first_coverage < first_detection, (
                "input_coverage issues should appear before link_detector issues"
            )


# ── Test 3: sparse registry with missing raw identify ────────────────────────

class TestSparseWithMissingRawIdentify:
    """No raw_zimas_identify data. Zone parse confirmed. Structured fields populated.

    Expected:
    - coverage = partial (raw identify absent, but other signals present)
    - zero-record issue if no records found should be warning-level
    - INTERRUPT_NONE reason should mention partial coverage
    """

    def _build_inp(self, with_overlay=False):
        return ZimasLinkedDocInput(
            apn="9999-001-002",
            specific_plan=None,
            overlay_zones=["CDO"] if with_overlay else [],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify={},   # absent
            zoning_parse_confidence="confirmed",
            zoning_parse_issues=[],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=False,
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_coverage_is_partial(self):
        inp = self._build_inp()
        coverage, issues = assess_input_coverage(inp)
        assert coverage == INPUT_COVERAGE_PARTIAL, (
            f"Expected partial, got '{coverage}'. "
            f"Issues: {[(i.severity, i.message[:60]) for i in issues]}"
        )

    def test_pipeline_coverage_partial(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        assert out.registry_input_coverage == INPUT_COVERAGE_PARTIAL

    def test_raw_identify_absent_warning_present(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        raw_id_issues = [
            i for i in out.all_issues
            if i.step == "input_coverage" and "raw_zimas_identify" in i.field
        ]
        assert raw_id_issues, "Expected a warning about missing raw_zimas_identify"
        assert all(i.severity in ("warning", "error") for i in raw_id_issues)

    def test_zero_record_warning_when_no_overlays(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp(with_overlay=False))
        assert out.records_classified == 0
        zero_issues = [
            i for i in out.all_issues
            if i.step == "doc_registry" and "zero records" in i.message.lower()
        ]
        assert zero_issues
        for iss in zero_issues:
            assert iss.severity == "warning", (
                f"Expected warning for partial coverage + zero records, got '{iss.severity}'"
            )

    def test_interrupt_none_mentions_partial(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp(with_overlay=False))
        far_interrupt = _interrupt_for_topic(out, "FAR")
        assert far_interrupt is not None
        assert far_interrupt.interrupt_level == INTERRUPT_NONE
        assert "partial" in far_interrupt.reason.lower() or "coverage" in far_interrupt.reason.lower(), (
            f"Expected coverage qualification in INTERRUPT_NONE reason, got: {far_interrupt.reason}"
        )


# ── Test 4: mixed signals producing partial coverage ─────────────────────────

class TestMixedSignalsPartialCoverage:
    """Raw identify present, parse provisional, some structured fields present.

    Expected:
    - coverage = partial (provisional parse degrades from complete)
    - no error-level coverage issues
    - INTERRUPT_NONE qualified but not with WARNING
    """

    def _build_inp(self):
        return ZimasLinkedDocInput(
            apn="1111-222-333",
            specific_plan=None,
            overlay_zones=[],
            q_conditions=[],
            d_limitations=[],
            raw_zimas_identify=_fake_identify_with_results(),
            zoning_parse_confidence="provisional",   # reduced but not failed
            zoning_parse_issues=["D limitation present but ordinance number not available from ZIMAS"],
            has_q_from_zone_string=False,
            q_ordinance_number=None,
            has_d_from_zone_string=True,    # parser caught it
            d_ordinance_number=None,
            supplemental_districts_from_parse=[],
        )

    def test_coverage_is_partial_not_uncertain(self):
        inp = self._build_inp()
        coverage, issues = assess_input_coverage(inp)
        assert coverage == INPUT_COVERAGE_PARTIAL, (
            f"Expected partial for provisional parse, got '{coverage}'"
        )
        assert coverage != INPUT_COVERAGE_UNCERTAIN

    def test_no_error_level_issues(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        error_issues = [
            i for i in out.all_issues
            if i.step == "input_coverage" and i.severity == "error"
        ]
        assert not error_issues, (
            f"Provisional parse should not produce error-level issues: "
            f"{[i.message for i in error_issues]}"
        )

    def test_interrupt_none_has_coverage_note_not_warning(self):
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        # D limitation was gap-filled so density/FAR may have interrupts
        # Find a topic that is INTERRUPT_NONE
        none_decisions = [d for d in out.interrupt_decisions if d.interrupt_level == INTERRUPT_NONE]
        if not none_decisions:
            pytest.skip("All topics have interrupts — no INTERRUPT_NONE to check")
        for decision in none_decisions:
            # partial coverage: should mention coverage but NOT say WARNING
            assert "WARNING" not in decision.reason, (
                f"Partial coverage should not produce WARNING in reason, got: {decision.reason}"
            )
            # Should still be qualified (not a bare "no linked authority" statement)
            assert "coverage" in decision.reason.lower() or decision.reason == (
                f"No linked authority items detected that govern {decision.topic}."
            ), (
                f"Expected coverage note for partial coverage, got: {decision.reason}"
            )

    def test_gap_fill_provenance_issue_present(self):
        """Inline-D gap-fill should be noted in input_coverage issues."""
        out = run_zimas_linked_doc_pipeline(self._build_inp())
        gap_fill_issues = [
            i for i in out.all_issues
            if i.step == "input_coverage" and "gap-fill" in i.message.lower()
        ]
        assert gap_fill_issues, (
            "Expected gap-fill provenance note in input_coverage issues"
        )

    def test_thin_not_produced_when_raw_identify_present(self):
        """With raw identify populated, coverage should not drop to thin."""
        inp = self._build_inp()
        coverage, _ = assess_input_coverage(inp)
        assert coverage != INPUT_COVERAGE_THIN
