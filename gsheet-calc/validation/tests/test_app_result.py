"""Tests for AppResult aggregation and app_orchestrator.run_app().

Two layers:

1. Unit tests for AppResult.from_module_results() — use constructed
   ModuleResult objects to test aggregation logic in isolation. No
   FAR engine or ZIMAS pipeline is exercised here.

2. Smoke test for run_app() — wires the real orchestrator end-to-end
   with a minimal known-good site and verifies the contract holds
   (correct types, no crashes, key fields populated).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from models.result_common import (
    ActionPosture,
    AppResult,
    AppSummary,
    CoverageLevel,
    ConfidenceLevel,
    DecisionPosture,
    Interpretation,
    ModuleResult,
    OverallReadiness,
    RunStatus,
)
from models.project import Project
from validation.fixtures.sites import far_c2_1_no_overrides


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok_module(name: str) -> ModuleResult:
    """Minimal ModuleResult that is clean / complete / high-confidence."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.OK,
        coverage_level=CoverageLevel.COMPLETE,
        confidence=ConfidenceLevel.HIGH,
        interpretation=Interpretation(
            summary=f"{name}: ok",
            plain_language_result=f"{name} passed.",
            action_posture=ActionPosture.CAN_RELY_WITH_REVIEW,
        ),
    )


def _partial_module(name: str) -> ModuleResult:
    """ModuleResult with partial coverage but no blocking."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.PARTIAL,
        coverage_level=CoverageLevel.PARTIAL,
        confidence=ConfidenceLevel.MEDIUM,
        interpretation=Interpretation(
            summary=f"{name}: partial",
            plain_language_result=f"{name} partially resolved.",
            action_posture=ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS,
        ),
    )


def _blocked_module(name: str) -> ModuleResult:
    """ModuleResult that is blocking (run_status=BLOCKED, blocking=True)."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.BLOCKED,
        coverage_level=CoverageLevel.THIN,
        confidence=ConfidenceLevel.UNRESOLVED,
        blocking=True,
        interpretation=Interpretation(
            summary=f"{name}: blocked",
            plain_language_result=f"{name} is blocked.",
            action_posture=ActionPosture.MANUAL_INPUT_REQUIRED,
        ),
    )


def _error_module(name: str) -> ModuleResult:
    """ModuleResult that errored."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.ERROR,
        coverage_level=CoverageLevel.NONE,
        confidence=ConfidenceLevel.UNRESOLVED,
        blocking=True,
        interpretation=Interpretation(
            summary=f"{name}: error",
            plain_language_result=f"{name} encountered an error.",
            action_posture=ActionPosture.MANUAL_INPUT_REQUIRED,
        ),
    )


def _manual_input_module(name: str) -> ModuleResult:
    """ModuleResult that signals MANUAL_INPUT_REQUIRED via action_posture."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.PARTIAL,
        coverage_level=CoverageLevel.PARTIAL,
        confidence=ConfidenceLevel.LOW,
        interpretation=Interpretation(
            summary=f"{name}: needs input",
            plain_language_result=f"{name} requires manual input.",
            action_posture=ActionPosture.MANUAL_INPUT_REQUIRED,
        ),
    )


def _authority_module(name: str) -> ModuleResult:
    """ModuleResult that signals AUTHORITY_CONFIRMATION_REQUIRED."""
    return ModuleResult(
        module=name,
        run_status=RunStatus.PARTIAL,
        coverage_level=CoverageLevel.PARTIAL,
        confidence=ConfidenceLevel.LOW,
        interpretation=Interpretation(
            summary=f"{name}: needs authority",
            plain_language_result=f"{name} requires authority confirmation.",
            action_posture=ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED,
        ),
    )


# ── AppResult aggregation unit tests ──────────────────────────────────────────

class TestAllOk:
    """Both modules clean → READY_FOR_DRAFT, overall_status=OK."""

    def test_overall_status(self):
        result = AppResult.from_module_results(
            project_id="p1",
            module_results=[_ok_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_status == RunStatus.OK

    def test_readiness(self):
        result = AppResult.from_module_results(
            project_id="p1",
            module_results=[_ok_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_readiness == OverallReadiness.READY_FOR_DRAFT

    def test_can_generate_draft(self):
        result = AppResult.from_module_results(
            project_id="p1",
            module_results=[_ok_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.decision_posture.can_generate_draft_g_sheet is True
        assert result.decision_posture.requires_manual_review_before_issue is False

    def test_summary_confirmed_both(self):
        result = AppResult.from_module_results(
            project_id="p1",
            module_results=[_ok_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert set(result.summary.confirmed) == {"far", "zimas_linked_docs"}
        assert result.summary.unresolved == []

    def test_no_blocking_modules(self):
        result = AppResult.from_module_results(
            project_id="p1",
            module_results=[_ok_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.decision_posture.blocking_modules == []


class TestOnePartial:
    """One partial module → DRAFT_ONLY, overall_status=PARTIAL."""

    def test_overall_status(self):
        result = AppResult.from_module_results(
            project_id="p2",
            module_results=[_ok_module("far"), _partial_module("zimas_linked_docs")],
        )
        assert result.overall_status == RunStatus.PARTIAL

    def test_readiness(self):
        result = AppResult.from_module_results(
            project_id="p2",
            module_results=[_ok_module("far"), _partial_module("zimas_linked_docs")],
        )
        assert result.overall_readiness == OverallReadiness.DRAFT_ONLY_NOT_FOR_ISSUE

    def test_can_generate_draft(self):
        result = AppResult.from_module_results(
            project_id="p2",
            module_results=[_ok_module("far"), _partial_module("zimas_linked_docs")],
        )
        assert result.decision_posture.can_generate_draft_g_sheet is True
        assert result.decision_posture.requires_manual_review_before_issue is True

    def test_non_blocking_uncertainties(self):
        result = AppResult.from_module_results(
            project_id="p2",
            module_results=[_ok_module("far"), _partial_module("zimas_linked_docs")],
        )
        assert result.decision_posture.non_blocking_uncertainties == ["zimas_linked_docs"]

    def test_unresolved_contains_partial_module(self):
        result = AppResult.from_module_results(
            project_id="p2",
            module_results=[_ok_module("far"), _partial_module("zimas_linked_docs")],
        )
        assert "zimas_linked_docs" in result.summary.unresolved
        assert "far" in result.summary.confirmed


class TestOneBlocked:
    """One blocking module → NOT_READY_FOR_PERMIT, overall_status=BLOCKED."""

    def test_overall_status(self):
        result = AppResult.from_module_results(
            project_id="p3",
            module_results=[_blocked_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_status == RunStatus.BLOCKED

    def test_readiness(self):
        result = AppResult.from_module_results(
            project_id="p3",
            module_results=[_blocked_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_readiness == OverallReadiness.NOT_READY_FOR_PERMIT

    def test_cannot_generate_draft(self):
        result = AppResult.from_module_results(
            project_id="p3",
            module_results=[_blocked_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.decision_posture.can_generate_draft_g_sheet is False

    def test_blocking_module_listed(self):
        result = AppResult.from_module_results(
            project_id="p3",
            module_results=[_blocked_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.decision_posture.blocking_modules == ["far"]

    def test_project_id_preserved(self):
        result = AppResult.from_module_results(
            project_id="p3",
            module_results=[_blocked_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.project_id == "p3"


class TestOneError:
    """One errored module → NOT_READY_FOR_PERMIT, overall_status=ERROR."""

    def test_overall_status(self):
        result = AppResult.from_module_results(
            project_id="p4",
            module_results=[_error_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_status == RunStatus.ERROR

    def test_readiness(self):
        result = AppResult.from_module_results(
            project_id="p4",
            module_results=[_error_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_readiness == OverallReadiness.NOT_READY_FOR_PERMIT

    def test_cannot_generate_draft(self):
        result = AppResult.from_module_results(
            project_id="p4",
            module_results=[_error_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.decision_posture.can_generate_draft_g_sheet is False


class TestManualInputRequired:
    """Module with MANUAL_INPUT_REQUIRED posture → INSUFFICIENT_INPUT readiness.

    This verifies the action_posture path in requires_manual_input(),
    since adapters signal this via posture rather than Issue objects.
    """

    def test_readiness_is_insufficient_input(self):
        result = AppResult.from_module_results(
            project_id="p5",
            module_results=[_manual_input_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert result.overall_readiness == OverallReadiness.INSUFFICIENT_INPUT

    def test_appears_in_manual_inputs_required(self):
        result = AppResult.from_module_results(
            project_id="p5",
            module_results=[_manual_input_module("far"), _ok_module("zimas_linked_docs")],
        )
        assert "far" in result.summary.manual_inputs_required

    def test_insufficient_input_beats_blocked(self):
        """INSUFFICIENT_INPUT is checked before BLOCKED in _derive_readiness."""
        result = AppResult.from_module_results(
            project_id="p5",
            module_results=[
                _manual_input_module("far"),
                _blocked_module("zimas_linked_docs"),
            ],
        )
        assert result.overall_readiness == OverallReadiness.INSUFFICIENT_INPUT


class TestAuthorityConfirmationRequired:
    """Module with AUTHORITY_CONFIRMATION_REQUIRED posture → surfaces in summary."""

    def test_appears_in_authority_confirmations_required(self):
        result = AppResult.from_module_results(
            project_id="p6",
            module_results=[_authority_module("zimas_linked_docs"), _ok_module("far")],
        )
        assert "zimas_linked_docs" in result.summary.authority_confirmations_required

    def test_not_in_manual_inputs_required(self):
        result = AppResult.from_module_results(
            project_id="p6",
            module_results=[_authority_module("zimas_linked_docs"), _ok_module("far")],
        )
        assert "zimas_linked_docs" not in result.summary.manual_inputs_required


class TestModuleResultsPreserved:
    """AppResult stores the original ModuleResult list."""

    def test_module_results_count(self):
        mods = [_ok_module("far"), _partial_module("zimas_linked_docs")]
        result = AppResult.from_module_results(project_id="p7", module_results=mods)
        assert len(result.module_results) == 2

    def test_module_names_preserved(self):
        mods = [_ok_module("far"), _partial_module("zimas_linked_docs")]
        result = AppResult.from_module_results(project_id="p7", module_results=mods)
        names = {m.module for m in result.module_results}
        assert names == {"far", "zimas_linked_docs"}


class TestAppResultSchema:
    """AppResult fields satisfy the Pydantic contract."""

    def test_is_app_result_instance(self):
        result = AppResult.from_module_results(
            project_id="schema-check",
            module_results=[_ok_module("far")],
        )
        assert isinstance(result, AppResult)
        assert isinstance(result.summary, AppSummary)
        assert isinstance(result.decision_posture, DecisionPosture)

    def test_app_version_default(self):
        result = AppResult.from_module_results(
            project_id="schema-check",
            module_results=[_ok_module("far")],
        )
        assert result.app_version == "v1"


# ── Orchestrator smoke test ───────────────────────────────────────────────────

class TestRunAppSmoke:
    """End-to-end smoke: run_app() with a real site returns a valid AppResult.

    Uses far_c2_1_no_overrides (C2-1 site, no overlays, no architect floor area)
    which is known to produce a deterministic PARTIAL FAR result with no overrides.
    The ZIMAS pipeline runs with minimal inputs derived from the site.
    """

    def setup_method(self):
        from analysis.app_orchestrator import run_app
        site = far_c2_1_no_overrides()
        project = Project(project_name="Smoke Test")
        self.result = run_app(site, project, project_id="smoke-001")

    def test_returns_app_result(self):
        assert isinstance(self.result, AppResult)

    def test_project_id(self):
        assert self.result.project_id == "smoke-001"

    def test_has_five_module_results(self):
        assert len(self.result.module_results) == 5

    def test_module_names(self):
        names = {m.module for m in self.result.module_results}
        assert names == {"far", "zimas_linked_docs", "density", "parking", "setback"}

    def test_overall_status_is_valid(self):
        assert self.result.overall_status in set(RunStatus)

    def test_overall_readiness_is_valid(self):
        assert self.result.overall_readiness in set(OverallReadiness)

    def test_decision_posture_populated(self):
        dp = self.result.decision_posture
        assert isinstance(dp.can_generate_draft_g_sheet, bool)
        assert isinstance(dp.requires_manual_review_before_issue, bool)

    def test_summary_lists_are_populated(self):
        s = self.result.summary
        # All module names should appear in either confirmed or unresolved
        all_tracked = set(s.confirmed) | set(s.unresolved)
        assert "far" in all_tracked
        assert "zimas_linked_docs" in all_tracked
        assert "density" in all_tracked
        assert "parking" in all_tracked
        assert "setback" in all_tracked

    def test_no_modules_in_both_confirmed_and_unresolved(self):
        s = self.result.summary
        overlap = set(s.confirmed) & set(s.unresolved)
        assert overlap == set(), f"Modules in both confirmed and unresolved: {overlap}"
