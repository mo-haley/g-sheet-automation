"""Tests for run_density_module() — ModuleResult adapter behavior.

Covers:
    development_posture derivation
    coverage_level  (uncertain×2, thin, partial, complete)
    run_status      (partial — ok is aspirational until prior-entitlement check is added)
    confidence      (medium, unresolved)
    blocking        (true / false boundary)
    action_posture  (manual_input, act_on_detected, can_rely)
    candidate_routes (baseline always, TOC when eligible, State DB by posture)
    excluded_routes  (posture-filtered lanes preserved with reasons)
    decision-grade mode vs comparison mode
    module_payload structure
    module name / version / schema

The density engine (run_density) is not re-tested here — see test_density.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from density.density_orchestrator import derive_development_posture, run_density_module
from models.project import AffordabilityPlan, Project
from models.result_common import (
    ActionPosture,
    ConfidenceLevel,
    CoverageLevel,
    ModuleResult,
    RunStatus,
)
from models.site import Site
from validation.fixtures.sites import c2_residential_site, simple_r3_site


# ── Shared helpers ────────────────────────────────────────────────────────────


def _plain_project() -> Project:
    return Project(project_name="Test Project")


def _mixed_project() -> Project:
    return Project(
        project_name="Mixed Project",
        affordability=AffordabilityPlan(li_pct=15.0, market_pct=85.0),
    )


def _affordable_100_project() -> Project:
    return Project(
        project_name="100% Affordable",
        affordability=AffordabilityPlan(
            eli_pct=10.0, vli_pct=15.0, li_pct=75.0, market_pct=0.0
        ),
    )


def _no_zone_site() -> Site:
    """No zone at all — zone lookup fails → UNCERTAIN, blocking."""
    return Site(address="Test No Zone", lot_area_sf=7500.0)


def _cm_zone_site() -> Site:
    """CM zone — ambiguous density factor (two candidates) → UNCERTAIN, blocking."""
    return Site(
        address="Test CM",
        zone="CM",
        height_district="1",
        zone_code_chapter="chapter_1",
        lot_area_sf=7500.0,
    )


def _no_lot_area_site() -> Site:
    """Known zone, no lot area → THIN, not blocking."""
    return Site(
        address="Test No Lot Area",
        zone="R3",
        height_district="1",
        zone_code_chapter="chapter_1",
    )


def _r3_with_specific_plan() -> Site:
    """R3 + specific plan → authority interrupter present → PARTIAL."""
    return Site(
        address="Test SP",
        zone="R3",
        height_district="1",
        zone_code_chapter="chapter_1",
        lot_area_sf=7500.0,
        specific_plan="Exposition Corridor Specific Plan",
    )


# ── Development posture derivation ───────────────────────────────────────────


class TestDerivePosture:
    """derive_development_posture() maps project fields to posture strings."""

    def test_no_affordability_is_market_rate(self):
        assert derive_development_posture(_plain_project()) == "market_rate"

    def test_partial_affordability_is_mixed(self):
        assert derive_development_posture(_mixed_project()) == "mixed"

    def test_100_affordable_is_affordable_100(self):
        assert derive_development_posture(_affordable_100_project()) == "affordable_100"

    def test_zero_filled_affordability_plan_is_market_rate(self):
        project = Project(
            project_name="Zero",
            affordability=AffordabilityPlan(market_pct=100.0),
        )
        assert derive_development_posture(project) == "market_rate"

    def test_override_takes_priority(self):
        assert derive_development_posture(_plain_project(), override="affordable_100") == "affordable_100"

    def test_unrecognized_override_returns_unknown(self):
        assert derive_development_posture(_plain_project(), override="hedge_fund") == "unknown"


# ── Coverage: UNCERTAIN ───────────────────────────────────────────────────────


class TestUncertainCoverageNoZone:
    """No zone string → zone lookup fails → UNCERTAIN, blocking."""

    def _run(self):
        return run_density_module(_no_zone_site(), _plain_project())

    def test_coverage_uncertain(self):
        assert self._run().coverage_level == CoverageLevel.UNCERTAIN

    def test_blocking_true(self):
        assert self._run().blocking is True

    def test_confidence_unresolved(self):
        assert self._run().confidence == ConfidenceLevel.UNRESOLVED

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL


class TestUncertainCoverageCMZone:
    """CM zone → ambiguous density factor → UNCERTAIN, blocking."""

    def _run(self):
        return run_density_module(_cm_zone_site(), _plain_project())

    def test_coverage_uncertain(self):
        assert self._run().coverage_level == CoverageLevel.UNCERTAIN

    def test_blocking_true(self):
        assert self._run().blocking is True

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED


# ── Coverage: THIN ────────────────────────────────────────────────────────────


class TestThinCoverage:
    """Known zone, no lot area → THIN, not blocking."""

    def _run(self):
        return run_density_module(_no_lot_area_site(), _plain_project())

    def test_coverage_thin(self):
        assert self._run().coverage_level == CoverageLevel.THIN

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_action_posture_manual_input(self):
        # Lot area missing — can't compute units — requires user input.
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL


# ── Coverage: PARTIAL ─────────────────────────────────────────────────────────


class TestPartialCoverageWithInterrupter:
    """R3 site with specific plan → authority interrupter → PARTIAL."""

    def _run(self):
        return run_density_module(_r3_with_specific_plan(), _plain_project())

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_action_posture_act_on_detected(self):
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
        )

    def test_specific_plan_in_inputs_summary(self):
        result = self._run()
        assert result.inputs_summary["specific_plan"] == "Exposition Corridor Specific Plan"


# ── Coverage: COMPLETE — comparison mode ─────────────────────────────────────


class TestComparisonModeClean:
    """Clean R3 site, market_rate posture, no TOC tier.
    Comparison mode: baseline only (no eligible alternatives).
    """

    def _run(self):
        return run_density_module(simple_r3_site(), _plain_project())

    def test_coverage_complete(self):
        assert self._run().coverage_level == CoverageLevel.COMPLETE

    def test_run_status_partial(self):
        # Prior entitlements are never checked, so status stays provisional → PARTIAL.
        assert self._run().run_status == RunStatus.PARTIAL

    def test_action_posture_act_on_detected(self):
        # Comparison mode always returns ACT_ON_DETECTED — lane selection is open.
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
        )

    def test_operating_mode_is_comparison(self):
        assert self._run().module_payload["operating_mode"] == "comparison"

    def test_baseline_route_present(self):
        routes = self._run().module_payload["candidate_routes"]
        lanes = [r["lane"] for r in routes]
        assert "none" in lanes

    def test_toc_excluded_no_tier(self):
        excluded = self._run().module_payload["excluded_routes"]
        excluded_lanes = [r["lane"] for r in excluded]
        assert "toc" in excluded_lanes

    def test_state_db_excluded_market_rate(self):
        excluded = self._run().module_payload["excluded_routes"]
        excluded_lanes = [r["lane"] for r in excluded]
        assert "state_db" in excluded_lanes

    def test_baseline_units_in_plain_language(self):
        result = self._run()
        # Baseline units for R3, 7500 sf lot: floor(7500/800) = 9
        assert "9" in result.interpretation.plain_language_result
        assert "Baseline" in result.interpretation.plain_language_result


# ── Decision-grade mode ───────────────────────────────────────────────────────


class TestDecisionGradeCleanBaseline:
    """Clean R3 site, lane='none' (baseline only), decision-grade."""

    def _run(self):
        return run_density_module(simple_r3_site(), _plain_project(), selected_lane="none")

    def test_operating_mode_is_decision_grade(self):
        assert self._run().module_payload["operating_mode"] == "decision_grade"

    def test_action_posture_can_rely(self):
        # Decision-grade, no SP/CPIO/D/Q interrupters, status not unresolved.
        assert (
            self._run().interpretation.action_posture == ActionPosture.CAN_RELY_WITH_REVIEW
        )

    def test_coverage_complete(self):
        assert self._run().coverage_level == CoverageLevel.COMPLETE

    def test_selected_route_populated(self):
        result = self._run()
        # selected_lane="none" → selected_route is the baseline route
        assert result.module_payload["selected_route"] is not None
        assert result.module_payload["selected_route"]["lane"] == "none"

    def test_excluded_routes_empty_decision_grade(self):
        # Decision-grade mode does not populate excluded_routes.
        assert self._run().module_payload["excluded_routes"] == []


# ── TOC candidate route ───────────────────────────────────────────────────────


class TestTOCCandidateRoute:
    """C2 site with TOC tier 3, market_rate posture.
    TOC should appear as a candidate route; State DB should be excluded.
    """

    def _run(self):
        return run_density_module(c2_residential_site(), _plain_project())

    def test_toc_in_candidate_routes(self):
        routes = self._run().module_payload["candidate_routes"]
        lanes = [r["lane"] for r in routes]
        assert "toc" in lanes

    def test_toc_units_exceed_baseline(self):
        routes = self._run().module_payload["candidate_routes"]
        baseline = next(r for r in routes if r["lane"] == "none")
        toc = next(r for r in routes if r["lane"] == "toc")
        if toc["status"] == "computed" and toc["units"] is not None and baseline["units"] is not None:
            assert toc["units"] > baseline["units"]

    def test_state_db_excluded_market_rate(self):
        excluded = self._run().module_payload["excluded_routes"]
        excluded_lanes = [r["lane"] for r in excluded]
        assert "state_db" in excluded_lanes

    def test_toc_in_plain_language_result(self):
        result = self._run()
        # TOC is the strongest candidate — should appear in the summary.
        assert "TOC" in result.interpretation.plain_language_result

    def test_excluded_routes_have_reasons(self):
        excluded = self._run().module_payload["excluded_routes"]
        for route in excluded:
            assert route["reason"], f"Route {route['lane']} has no reason"


# ── State DB by posture ───────────────────────────────────────────────────────


class TestStateDBAInclusionMixed:
    """Mixed posture → State DB included in candidate routes."""

    def _run(self):
        return run_density_module(simple_r3_site(), _mixed_project())

    def test_state_db_in_candidate_routes(self):
        routes = self._run().module_payload["candidate_routes"]
        lanes = [r["lane"] for r in routes]
        assert "state_db" in lanes

    def test_development_posture_is_mixed(self):
        assert self._run().module_payload["development_posture"] == "mixed"


class TestStateDBAInclusionAffordable100:
    """affordable_100 posture → State DB included."""

    def _run(self):
        return run_density_module(simple_r3_site(), _affordable_100_project())

    def test_state_db_in_candidate_routes(self):
        routes = self._run().module_payload["candidate_routes"]
        lanes = [r["lane"] for r in routes]
        assert "state_db" in lanes

    def test_development_posture_is_affordable_100(self):
        assert self._run().module_payload["development_posture"] == "affordable_100"


# ── Module payload structure ──────────────────────────────────────────────────


class TestModulePayloadStructure:
    """module_payload has the required keys in comparison mode."""

    def setup_method(self):
        self.payload = run_density_module(simple_r3_site(), _plain_project()).module_payload

    def test_required_top_level_keys(self):
        for key in ("operating_mode", "development_posture", "baseline",
                    "candidate_routes", "excluded_routes", "selected_route", "full_output"):
            assert key in self.payload, f"Missing key: {key}"

    def test_baseline_has_required_fields(self):
        b = self.payload["baseline"]
        for key in ("units", "sf_per_du", "lot_area_sf", "status"):
            assert key in b, f"Missing baseline key: {key}"

    def test_candidate_routes_have_required_fields(self):
        for route in self.payload["candidate_routes"]:
            for key in ("lane", "units", "unlimited", "status", "confidence", "gap_reasons"):
                assert key in route, f"Route missing key: {key}"

    def test_full_output_is_dict(self):
        assert isinstance(self.payload["full_output"], dict)

    def test_selected_route_is_none_comparison_mode(self):
        assert self.payload["selected_route"] is None


# ── Module result schema ──────────────────────────────────────────────────────


class TestModuleResultSchema:
    """ModuleResult envelope contract."""

    def _run(self):
        return run_density_module(simple_r3_site(), _plain_project())

    def test_is_module_result(self):
        assert isinstance(self._run(), ModuleResult)

    def test_module_name_is_density(self):
        assert self._run().module == "density"

    def test_module_version_default(self):
        assert self._run().module_version == "v1"

    def test_inputs_summary_has_base_zone(self):
        result = self._run()
        assert result.inputs_summary["base_zone"] == "R3"

    def test_inputs_summary_has_operating_mode(self):
        result = self._run()
        assert result.inputs_summary["operating_mode"] == "comparison"

    def test_provenance_populated(self):
        result = self._run()
        assert "zimas_parcel_data" in result.provenance.authoritative_sources_used
        assert "lamc_density_table" in result.provenance.authoritative_sources_used

    def test_full_output_preserved_in_payload(self):
        result = self._run()
        assert "density_result" in result.module_payload["full_output"]
        assert "baseline_density" in result.module_payload["full_output"]
