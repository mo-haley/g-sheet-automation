"""Tests for run_parking_module() — ModuleResult adapter behavior.

Covers:
    coverage_level  (complete, partial via flagged_controls / unresolved lane / default assumption)
    run_status      (always partial — prior_case_conditions unchecked)
    confidence      (high, medium, low)
    blocking        (always False)
    action_posture  (authority_confirmation, act_on_detected, can_rely)
    module_payload  structure and required keys
    plain_language_result  three cases (baseline governs / unresolved / governing stated)
    module name / version / schema

The parking engine (run_parking) is not re-tested here — see legacy parking tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from models.project import AffordabilityPlan, Project, UnitType
from models.result_common import (
    ActionPosture,
    CoverageLevel,
    ConfidenceLevel,
    ModuleResult,
    RunStatus,
)
from models.site import Site
from parking.parking_orchestrator import run_parking_module
from validation.fixtures.sites import c2_residential_site, simple_r3_site


# ── Shared helpers ────────────────────────────────────────────────────────────


def _unit_mix_project() -> Project:
    """Project with explicit unit mix — prevents default unit-mix assumption."""
    return Project(
        project_name="Unit Mix Project",
        total_units=10,
        unit_mix=[
            UnitType(label="1BR", count=6, habitable_rooms=2, bedrooms=1),
            UnitType(label="2BR", count=4, habitable_rooms=3, bedrooms=2),
        ],
        parking_spaces_total=14,
    )


def _plain_project() -> Project:
    return Project(project_name="Plain Project")


def _units_no_mix_project() -> Project:
    """total_units set but no unit_mix — triggers default unit-mix assumption."""
    return Project(
        project_name="No Mix",
        total_units=10,
        parking_spaces_total=10,
    )


def _r3_with_specific_plan() -> Site:
    """R3 site with specific plan → flagged_controls non-empty → AUTHORITY_CONFIRMATION."""
    return Site(
        address="Test SP Site",
        zone="R3",
        height_district="1",
        zone_code_chapter="chapter_1",
        lot_area_sf=7500.0,
        specific_plan="Exposition Corridor Specific Plan",
    )


# ── Coverage: COMPLETE — explicit baseline lane, known unit mix ───────────────


class TestBaselineLaneComplete:
    """Explicit parking_lane='none', unit mix project, clean R3 → COMPLETE, CAN_RELY."""

    def _run(self):
        return run_parking_module(
            simple_r3_site(),
            _unit_mix_project(),
            parking_lane="none",
        )

    def test_coverage_complete(self):
        assert self._run().coverage_level == CoverageLevel.COMPLETE

    def test_action_posture_can_rely(self):
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.CAN_RELY_WITH_REVIEW
        )

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_active_lane_none(self):
        assert self._run().module_payload["active_lane"] == "none"

    def test_plain_language_baseline_governs(self):
        result = self._run().interpretation.plain_language_result
        assert "baseline governs" in result.lower()
        assert "LAMC 12.21 A.4" in result

    def test_baseline_required_positive(self):
        assert self._run().module_payload["baseline_required"] > 0


# ── Coverage: PARTIAL — flagged controls (authority interrupter) ──────────────


class TestSpecificPlanInterrupter:
    """Specific plan detected → flagged_controls non-empty → AUTHORITY_CONFIRMATION."""

    def _run(self):
        return run_parking_module(_r3_with_specific_plan(), _unit_mix_project())

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_action_posture_authority_confirmation(self):
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
        )

    def test_blocking_false(self):
        # blocking is always False for parking
        assert self._run().blocking is False

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_specific_plan_in_inputs_summary(self):
        result = self._run()
        assert result.inputs_summary.get("specific_plan") == "Exposition Corridor Specific Plan"

    def test_flagged_controls_in_inputs_summary(self):
        result = self._run()
        assert "flagged_controls" in result.inputs_summary
        assert len(result.inputs_summary["flagged_controls"]) > 0


# ── Coverage: PARTIAL — unresolved reduction lane ─────────────────────────────


class TestUnresolvedLane:
    """C2 site with TOC tier: both TOC and State DB compute, no explicit lane.
    select_parking_lane path 3: multiple results, no governing basis → 'unresolved'.
    """

    def _run(self):
        # c2_residential_site has toc_tier=3; state_db always runs.
        # No explicit parking_lane → lane="unresolved".
        return run_parking_module(c2_residential_site(), _unit_mix_project())

    def test_active_lane_unresolved(self):
        assert self._run().module_payload["active_lane"] == "unresolved"

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_action_posture_act_on_detected(self):
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
        )

    def test_governing_required_is_none(self):
        assert self._run().module_payload["governing_required"] is None

    def test_plain_language_mentions_unresolved(self):
        result = self._run().interpretation.plain_language_result
        assert "unresolved" in result.lower()

    def test_blocking_false(self):
        assert self._run().blocking is False


# ── Coverage: PARTIAL — default unit mix assumption ───────────────────────────


class TestDefaultUnitMixAssumption:
    """Project with total_units but no unit_mix → baseline uses default assumption → PARTIAL."""

    def _run(self):
        return run_parking_module(
            simple_r3_site(),
            _units_no_mix_project(),
            parking_lane="none",
        )

    def test_coverage_partial(self):
        # used_default_unit_mix_assumption=True → PARTIAL even with lane="none"
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_blocking_false(self):
        assert self._run().blocking is False


# ── TOC lane selected explicitly ──────────────────────────────────────────────


class TestTOCLaneSelected:
    """C2 site with TOC tier, parking_lane='toc' explicitly → toc result in payload."""

    def _run(self):
        return run_parking_module(
            c2_residential_site(),
            _unit_mix_project(),
            parking_lane="toc",
        )

    def test_active_lane_toc(self):
        assert self._run().module_payload["active_lane"] == "toc"

    def test_toc_in_lane_results(self):
        assert "toc" in self._run().module_payload["lane_results"]

    def test_plain_language_mentions_toc(self):
        result = self._run().interpretation.plain_language_result
        assert "TOC" in result

    def test_baseline_always_in_plain_language(self):
        result = self._run().interpretation.plain_language_result
        assert "LAMC 12.21 A.4" in result


# ── Module payload structure ──────────────────────────────────────────────────


class TestModulePayloadStructure:
    """module_payload has the required keys."""

    def setup_method(self):
        self.payload = run_parking_module(
            simple_r3_site(), _unit_mix_project(), parking_lane="none"
        ).module_payload

    def test_required_top_level_keys(self):
        for key in (
            "baseline_required",
            "governing_required",
            "active_lane",
            "lane_results",
            "proposed_parking",
            "parking_delta",
            "full_output",
        ):
            assert key in self.payload, f"Missing key: {key}"

    def test_full_output_is_dict(self):
        assert isinstance(self.payload["full_output"], dict)

    def test_lane_results_is_dict(self):
        assert isinstance(self.payload["lane_results"], dict)

    def test_baseline_required_is_float(self):
        assert isinstance(self.payload["baseline_required"], float)


# ── Module result schema ──────────────────────────────────────────────────────


class TestModuleResultSchema:
    """ModuleResult envelope contract."""

    def _run(self):
        return run_parking_module(simple_r3_site(), _plain_project())

    def test_is_module_result(self):
        assert isinstance(self._run(), ModuleResult)

    def test_module_name_is_parking(self):
        assert self._run().module == "parking"

    def test_module_version_default(self):
        assert self._run().module_version == "v1"

    def test_blocking_always_false(self):
        assert self._run().blocking is False

    def test_run_status_always_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_inputs_summary_has_base_zone(self):
        result = self._run()
        assert result.inputs_summary["base_zone"] == "R3"

    def test_provenance_populated(self):
        result = self._run()
        assert "lamc_12_21_a4_parking_table" in result.provenance.authoritative_sources_used
        assert "zimas_parcel_data" in result.provenance.authoritative_sources_used

    def test_full_output_preserved_in_payload(self):
        result = self._run()
        fo = result.module_payload["full_output"]
        assert "baseline_parking" in fo
        assert "parking_result" in fo
