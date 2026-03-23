"""Tests for calculate_far_module() — ModuleResult adapter behavior.

Covers the mapping from FAROutput to ModuleResult for:
    coverage_level  (uncertain, thin×2, partial×2, complete)
    run_status      (ok, partial)
    confidence      (high, low, unresolved)
    blocking        (true / false — and the key nuance case)
    action_posture  (all five postures exercised)
    module_payload  (preservation of FAROutput)
    summary / plain_language_result wiring

All tests use calculate_far_module end-to-end.
The 10-step FAR engine is not re-tested here — see test_far.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.far import calculate_far_full, calculate_far_module
from models.project import Project
from models.result_common import ActionPosture, ConfidenceLevel, CoverageLevel, RunStatus
from models.site import Site
from validation.fixtures.sites import (
    far_c2_1_no_overrides,
    far_c2_1vl_cpio_missing_doc,
    far_c2_2d_cpio_tcc_beacon,
)


# ── Shared helpers ────────────────────────────────────────────────────────────


def _simple_project() -> Project:
    return Project(project_name="Test Project")


def _project_counted_fa_aligned() -> Project:
    """Explicit counted floor area; definition matches chapter_1 site (LAMC 12.03)."""
    return Project(
        project_name="Test Explicit FA",
        counted_floor_area_sf=46765.0,
        floor_area_definition_used="LAMC 12.03",
    )


def _project_counted_fa_no_def() -> Project:
    """Counted floor area provided; no definition string (avoids alignment check)."""
    return Project(project_name="Test No Def", counted_floor_area_sf=30000.0)


def _no_zone_string_site() -> Site:
    """No zone string, no zone, no height district.
    parse_zoning → zoning_string_raw is None → parse_confidence='unresolved' → UNCERTAIN.
    """
    return Site(address="Test", lot_area_sf=5000.0)


def _no_lot_area_site() -> Site:
    """Zone and HD present (parse succeeds), but no lot area.
    parcel.identity_confidence='unresolved' → THIN.
    Baseline FAR (1.5 for C2-1) is still found — governing ratio is not None.
    """
    return Site(
        address="Test",
        zone="C2",
        height_district="1",
        zone_code_chapter="chapter_1",
    )


def _unknown_zone_site() -> Site:
    """Zone present (parse confirmed) but not in the FAR lookup table.
    baseline_far.ratio=None → governing_far.applicable_ratio=None → THIN + blocking.
    """
    return Site(
        address="Test",
        zone="ZUNKWN",
        height_district="99",
        zone_code_chapter="chapter_1",
        lot_area_sf=5000.0,
    )


def _unknown_zone_with_sp_site() -> Site:
    """Unknown zone + specific plan.
    Parse confirmed → steps 3-4 run → override_present=True.
    No baseline found → governing_far.applicable_ratio=None.
    blocking=True + override_present=True → AUTHORITY_CONFIRMATION_REQUIRED.
    """
    return Site(
        address="Test",
        zone="ZUNKWN",
        height_district="99",
        zone_code_chapter="chapter_1",
        lot_area_sf=5000.0,
        specific_plan="Venice Specific Plan",
    )


# ── Test 1: uncertain coverage (no zone string) ───────────────────────────────


class TestCoverageUncertain:
    """No zone string → parse returns unresolved → engine early-returns.

    Expected mapping:
        coverage_level  = UNCERTAIN
        run_status      = PARTIAL
        confidence      = UNRESOLVED
        blocking        = True   (governing_far.applicable_ratio is None)
        action_posture  = MANUAL_INPUT_REQUIRED  (no overrides ran — steps 3-4 skipped)
    """

    def _run(self):
        return calculate_far_module(_no_zone_string_site(), _simple_project())

    def test_coverage_uncertain(self):
        assert self._run().coverage_level == CoverageLevel.UNCERTAIN

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_confidence_unresolved(self):
        assert self._run().confidence == ConfidenceLevel.UNRESOLVED

    def test_blocking_true(self):
        assert self._run().blocking is True

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED


# ── Test 2: thin coverage — no lot area ──────────────────────────────────────


class TestCoverageThinNoLotArea:
    """Zone and HD present (parse succeeds), but no lot area at all.

    The engine finds a baseline FAR ratio (1.5 for C2-1) but cannot compute
    an area basis. The governing ratio is not None, so the result is not blocking —
    the caller has a provisional number.

    Expected mapping:
        coverage_level  = THIN    (parcel.identity_confidence == 'unresolved')
        run_status      = PARTIAL
        confidence      = LOW     (outcome.confidence degrades via min_confidence)
        blocking        = False   (governing_far.applicable_ratio = 1.5, not None)
    """

    def _run(self):
        return calculate_far_module(_no_lot_area_site(), _simple_project())

    def test_coverage_thin(self):
        assert self._run().coverage_level == CoverageLevel.THIN

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_confidence_low(self):
        assert self._run().confidence == ConfidenceLevel.LOW

    def test_blocking_false(self):
        """Baseline ratio found despite missing lot area — not blocking."""
        assert self._run().blocking is False


# ── Test 3: thin coverage — zone not in lookup table ─────────────────────────


class TestCoverageThinNoBaseline:
    """Zone present (parse confirmed) but not in the FAR lookup table.

    No baseline ratio → no governing FAR figure at all.

    Expected mapping:
        coverage_level  = THIN    (baseline_far.ratio is None)
        blocking        = True    (governing_far.applicable_ratio is None)
        action_posture  = MANUAL_INPUT_REQUIRED  (no local overrides present)
    """

    def _run(self):
        return calculate_far_module(_unknown_zone_site(), _simple_project())

    def test_coverage_thin(self):
        assert self._run().coverage_level == CoverageLevel.THIN

    def test_blocking_true(self):
        """No baseline ratio and no local FAR — no usable governing figure."""
        assert self._run().blocking is True

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED


# ── Test 4: partial coverage — governing unresolved, provisional ratio ────────


class TestCoveragePartialGoverningUnresolved:
    """CPIO present but not parsed. Engine stores provisional baseline ratio as best-guess.

    This is the key blocking nuance: outcome.state='unresolved' but the engine
    records governing_far.applicable_ratio=1.5 (provisional baseline). Not blocking.
    Manual review is required because the CPIO has not been reviewed.

    Expected mapping:
        coverage_level  = PARTIAL  (governing_far.state == 'unresolved')
        run_status      = PARTIAL
        confidence      = UNRESOLVED  (outcome.state == 'unresolved')
        blocking        = False    (governing_far.applicable_ratio is not None)
        action_posture  = ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
                          (numerator resolved, outcome.requires_manual_review=True)
    """

    def _run(self):
        # Provide counted FA so numerator is not the limiting factor.
        # Use no-def project to avoid definition alignment complication on CPIO site.
        return calculate_far_module(
            far_c2_1vl_cpio_missing_doc(),
            _project_counted_fa_no_def(),
        )

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_confidence_unresolved(self):
        assert self._run().confidence == ConfidenceLevel.UNRESOLVED

    def test_blocking_false(self):
        """Provisional ratio exists — governing FAR is a best-guess, not missing."""
        assert self._run().blocking is False

    def test_action_posture_review_for_gaps(self):
        result = self._run()
        assert result.interpretation.action_posture == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS


# ── Test 5: partial coverage — governing confirmed, numerator missing ─────────


class TestCoveragePartialNumeratorUnresolved:
    """Governing FAR confirmed (clean baseline, no overrides) but no counted floor area.

    The allowable limit is known; the proposed side is empty.

    Expected mapping:
        coverage_level  = PARTIAL  (proposed.numerator_source == 'unresolved')
        confidence      = LOW      (know the allowable limit, cannot compare)
        blocking        = False    (governing ratio 1.5 exists)
        action_posture  = MANUAL_INPUT_REQUIRED  (architect must supply floor area)
    """

    def _run(self):
        return calculate_far_module(far_c2_1_no_overrides(), _simple_project())

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_confidence_low(self):
        """Allowable FAR confirmed; proposed unknown — confidence degrades to LOW."""
        assert self._run().confidence == ConfidenceLevel.LOW

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED


# ── Test 6: complete coverage — all resolved ──────────────────────────────────


class TestCoverageComplete:
    """Baseline confirmed, aligned definition, explicit counted floor area.

    Expected mapping:
        coverage_level  = COMPLETE
        run_status      = OK
        confidence      = HIGH
        blocking        = False
        action_posture  = CAN_RELY_WITH_REVIEW
    """

    def _run(self):
        return calculate_far_module(
            far_c2_1_no_overrides(),
            _project_counted_fa_aligned(),
        )

    def test_coverage_complete(self):
        assert self._run().coverage_level == CoverageLevel.COMPLETE

    def test_run_status_ok(self):
        assert self._run().run_status == RunStatus.OK

    def test_confidence_high(self):
        assert self._run().confidence == ConfidenceLevel.HIGH

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_action_posture_can_rely(self):
        assert self._run().interpretation.action_posture == ActionPosture.CAN_RELY_WITH_REVIEW


# ── Test 7: blocking nuance ───────────────────────────────────────────────────


class TestBlockingNuance:
    """The critical distinction: outcome.state='unresolved' does NOT always mean blocking.

    blocking=True only when governing_far.applicable_ratio is None — i.e., the
    module cannot provide any usable governing FAR figure at all.

    Cases:
        A. Unresolved overrides with provisional ratio stored → NOT blocking
        B. No baseline found, no overrides → ratio None → blocking, MANUAL_INPUT_REQUIRED
        C. No baseline + specific plan → ratio None + override_present → blocking,
           AUTHORITY_CONFIRMATION_REQUIRED
    """

    def test_unresolved_state_with_provisional_ratio_is_not_blocking(self):
        """C2-2D-CPIO: governing state='unresolved' but engine stores baseline as best-guess.

        The provisional ratio gives the caller a number to work with.
        This is the core nuance: unresolved governing state ≠ missing ratio.
        """
        raw = calculate_far_full(far_c2_2d_cpio_tcc_beacon(), _simple_project())
        assert raw.governing_far.applicable_ratio is not None, (
            "Fixture must carry a provisional ratio for this test to be meaningful"
        )
        result = calculate_far_module(far_c2_2d_cpio_tcc_beacon(), _simple_project())
        assert result.blocking is False

    def test_no_baseline_no_ratio_is_blocking(self):
        """Unknown zone → no baseline found → applicable_ratio=None → blocking."""
        result = calculate_far_module(_unknown_zone_site(), _simple_project())
        assert result.blocking is True

    def test_blocking_with_local_override_gives_authority_confirmation(self):
        """Unknown zone + specific plan: blocking=True and override_present=True.

        Posture escalates to AUTHORITY_CONFIRMATION_REQUIRED because a local
        authority document (specific plan) needs to be retrieved and parsed —
        not merely user-supplied input data.
        """
        result = calculate_far_module(_unknown_zone_with_sp_site(), _simple_project())
        assert result.blocking is True
        assert result.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED

    def test_blocking_without_local_override_gives_manual_input(self):
        """Unknown zone, no overrides: blocking=True → MANUAL_INPUT_REQUIRED."""
        result = calculate_far_module(_unknown_zone_site(), _simple_project())
        assert result.blocking is True
        assert result.interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED


# ── Test 8: definition mismatch → ACT_ON posture ─────────────────────────────


class TestDefinitionMismatch:
    """Governing FAR confirmed, counted floor area provided, but definition mismatch.

    proposed.definition_aligned=False demotes the posture from CAN_RELY to ACT_ON
    without making the result blocking. The numbers are present but the comparison
    is not valid until the architect re-counts with the correct definition.
    """

    def _run(self):
        project = Project(
            project_name="Mismatch",
            counted_floor_area_sf=30000.0,
            floor_area_definition_used="2020 LABC Ch.2",  # site is chapter_1 → LAMC 12.03
        )
        return calculate_far_module(far_c2_1_no_overrides(), project)

    def test_not_blocking(self):
        assert self._run().blocking is False

    def test_action_posture_review_for_gaps(self):
        result = self._run()
        assert result.interpretation.action_posture == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS

    def test_not_can_rely_with_review(self):
        """Definition mismatch must prevent CAN_RELY_WITH_REVIEW posture."""
        assert self._run().interpretation.action_posture != ActionPosture.CAN_RELY_WITH_REVIEW


# ── Test 9: module_payload preservation ──────────────────────────────────────


class TestModulePayloadPreservation:
    """module_payload must faithfully serialize the full FAROutput."""

    def _run(self):
        return calculate_far_module(
            far_c2_1_no_overrides(),
            _project_counted_fa_aligned(),
        )

    def test_payload_has_expected_top_level_keys(self):
        payload = self._run().module_payload
        for key in (
            "parcel", "zoning", "floor_area_definition", "local_controls",
            "baseline_far", "governing_far", "area_basis", "allowable",
            "proposed", "incentive", "outcome", "issues", "metadata",
        ):
            assert key in payload, f"module_payload missing key: {key!r}"

    def test_payload_governing_far_ratio_round_trips(self):
        """C2-1 baseline = 1.5:1 — payload must preserve the exact value."""
        payload = self._run().module_payload
        assert payload["governing_far"]["applicable_ratio"] == 1.5

    def test_payload_numerator_source_round_trips(self):
        payload = self._run().module_payload
        assert payload["proposed"]["numerator_source"] == "explicit_total"

    def test_payload_outcome_state_present(self):
        payload = self._run().module_payload
        assert payload["outcome"]["state"] == "baseline_confirmed"

    def test_payload_issues_is_list(self):
        payload = self._run().module_payload
        assert isinstance(payload["issues"], list)


# ── Test 10: ModuleResult schema and fixed fields ─────────────────────────────


class TestModuleResultSchema:
    """Fixed metadata fields and interpretation wiring."""

    def _run(self):
        return calculate_far_module(far_c2_1_no_overrides(), _simple_project())

    def test_module_name(self):
        assert self._run().module == "far"

    def test_module_version(self):
        assert self._run().module_version == "v1"

    def test_interpretation_fields_nonempty(self):
        result = self._run()
        assert result.interpretation.summary, "summary must not be empty"
        assert result.interpretation.plain_language_result, "plain_language_result must not be empty"

    def test_summary_contains_coverage(self):
        assert "coverage=" in self._run().interpretation.summary

    def test_summary_contains_governing_state(self):
        assert "governing=" in self._run().interpretation.summary

    def test_plain_language_result_states_governing_far(self):
        """plain_language_result must always lead with the governing FAR figure."""
        plain = self._run().interpretation.plain_language_result
        assert "Governing FAR" in plain

    def test_plain_language_result_notes_missing_numerator(self):
        """When numerator is unresolved, plain_language_result must say so explicitly."""
        result = calculate_far_module(far_c2_1_no_overrides(), _simple_project())
        assert "not provided by architect" in result.interpretation.plain_language_result

    def test_inputs_summary_has_base_zone(self):
        assert "base_zone" in self._run().inputs_summary

    def test_inputs_summary_has_numerator_source(self):
        assert "numerator_source" in self._run().inputs_summary

    def test_provenance_sources_populated_for_complete_run(self):
        """Complete run must credit ZIMAS parcel data and the FAR lookup table."""
        result = calculate_far_module(far_c2_1_no_overrides(), _simple_project())
        auth = result.provenance.authoritative_sources_used
        assert "zimas_parcel_data" in auth
        assert "lamc_far_table_2" in auth

    def test_module_result_dumps_cleanly(self):
        dumped = self._run().model_dump()
        assert dumped["module"] == "far"
        assert isinstance(dumped["module_payload"], dict)
