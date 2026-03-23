"""Tests for run_setback_module() — ModuleResult adapter behavior.

Covers:
    coverage_level  (uncertain, thin, partial, complete)
    run_status      (blocked, partial, ok)
    confidence      (high, medium, low, unresolved)
    blocking        (true only for early_exit / no code_family)
    action_posture  (manual_input, authority_confirmation, act_on_detected, can_rely)
    per-edge payload (edges list preserved; no single governing scalar)
    baseline_yard_family vs governing_yard_family both exposed
    module name / version / schema

The setback engine (run_setback) and all pipeline steps are not re-tested
here — see legacy setback tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from models.result_common import (
    ActionPosture,
    ConfidenceLevel,
    CoverageLevel,
    ModuleResult,
    RunStatus,
)
from setback.models import EdgeInput, SetbackProjectInputs
from setback.setback_orchestrator import run_setback_module


# ── Shared fixtures ───────────────────────────────────────────────────────────


def _empty_inputs() -> SetbackProjectInputs:
    """No edges — triggers THIN coverage when zone resolves."""
    return SetbackProjectInputs(lot_type="interior")


def _rear_side_inputs() -> SetbackProjectInputs:
    """Interior lot with rear + two side edges; no street front edge.

    Avoids prevailing_setback_flag=True so edge status can reach 'confirmed'.
    lot_width supplied to enable side-yard formula computation.
    """
    return SetbackProjectInputs(
        lot_type="interior",
        edges=[
            EdgeInput(edge_id="E_rear", edge_type="interior_rear"),
            EdgeInput(edge_id="E_side1", edge_type="interior"),
            EdgeInput(edge_id="E_side2", edge_type="interior"),
        ],
        number_of_stories=2,
        lot_width=50.0,
        lot_depth=150.0,
    )


def _standard_inputs() -> SetbackProjectInputs:
    """Corner lot with street front + alley rear + two interior sides."""
    return SetbackProjectInputs(
        lot_type="corner",
        edges=[
            EdgeInput(edge_id="E_front", edge_type="street", street_name="Main St"),
            EdgeInput(edge_id="E_street_side", edge_type="street", street_name="Cross St"),
            EdgeInput(edge_id="E_rear", edge_type="alley"),
            EdgeInput(edge_id="E_interior", edge_type="interior"),
        ],
        number_of_stories=3,
        lot_width=50.0,
        lot_depth=150.0,
    )


def _sls_inputs() -> SetbackProjectInputs:
    """Small lot subdivision — triggers early_exit."""
    return SetbackProjectInputs(
        lot_type="interior",
        small_lot_subdivision=True,
        edges=[
            EdgeInput(edge_id="E1", edge_type="street", street_name="Main St"),
        ],
    )


# ── UNCERTAIN: early_exit (small_lot_subdivision) ────────────────────────────


class TestEarlyExitSmallLot:
    """small_lot_subdivision=True triggers early_exit → UNCERTAIN, BLOCKED."""

    def _run(self):
        return run_setback_module(
            project_inputs=_sls_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )

    def test_coverage_uncertain(self):
        assert self._run().coverage_level == CoverageLevel.UNCERTAIN

    def test_run_status_blocked(self):
        assert self._run().run_status == RunStatus.BLOCKED

    def test_blocking_true(self):
        assert self._run().blocking is True

    def test_confidence_unresolved(self):
        assert self._run().confidence == ConfidenceLevel.UNRESOLVED

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_plain_language_mentions_early_exit(self):
        result = self._run()
        assert "not computed" in result.interpretation.plain_language_result.lower()

    def test_no_edges_in_payload(self):
        # Early exit — no edge values computed
        assert self._run().module_payload["edges"] == []


# ── UNCERTAIN: zone not found ─────────────────────────────────────────────────


class TestZoneNotFound:
    """Unknown zone → code_family is None → UNCERTAIN, BLOCKED."""

    def _run(self):
        return run_setback_module(
            project_inputs=_rear_side_inputs(),
            raw_zone="XXXX-1",
            base_zone="XXXX",
        )

    def test_coverage_uncertain(self):
        assert self._run().coverage_level == CoverageLevel.UNCERTAIN

    def test_run_status_blocked(self):
        assert self._run().run_status == RunStatus.BLOCKED

    def test_blocking_true(self):
        assert self._run().blocking is True

    def test_confidence_unresolved(self):
        assert self._run().confidence == ConfidenceLevel.UNRESOLVED

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_plain_language_mentions_zone_not_found(self):
        result = self._run()
        plr = result.interpretation.plain_language_result.lower()
        assert "not found" in plr or "zone" in plr

    def test_code_family_none_in_payload(self):
        # overall_status still "unresolved"; code_family absent
        assert self._run().module_payload["baseline_yard_family"] is None


# ── THIN: zone resolved, no edges ─────────────────────────────────────────────


class TestThinNoEdges:
    """Zone resolves but no edges supplied → THIN, PARTIAL, blocking=False."""

    def _run(self):
        return run_setback_module(
            project_inputs=_empty_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )

    def test_coverage_thin(self):
        assert self._run().coverage_level == CoverageLevel.THIN

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_blocking_false(self):
        # Zone resolved; caller just needs to supply edges
        assert self._run().blocking is False

    def test_action_posture_manual_input(self):
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_plain_language_no_edges_supplied(self):
        plr = self._run().interpretation.plain_language_result
        assert "No lot edges" in plr or "edges" in plr.lower()

    def test_baseline_yard_family_present(self):
        # Zone resolved → baseline family is known
        payload = self._run().module_payload
        assert payload["baseline_yard_family"] is not None
        assert payload["baseline_yard_family"] == "R3"

    def test_edges_list_empty(self):
        assert self._run().module_payload["edges"] == []


# ── PARTIAL: CM split condition ───────────────────────────────────────────────


class TestCMSplitCondition:
    """CM zone → split_condition → PARTIAL, MANUAL_INPUT_REQUIRED."""

    def _run(self):
        return run_setback_module(
            project_inputs=_standard_inputs(),
            raw_zone="CM-1",
            base_zone="CM",
        )

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_action_posture_manual_input(self):
        # CM split — cannot auto-select yard family
        assert self._run().interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED

    def test_cm_split_in_payload(self):
        assert self._run().module_payload["cm_split"] is True

    def test_governing_yard_family_none(self):
        # Split: governing family is unresolved
        assert self._run().module_payload["governing_yard_family"] is None

    def test_plain_language_mentions_split(self):
        plr = self._run().interpretation.plain_language_result.lower()
        assert "split" in plr or "unresolved" in plr


# ── PARTIAL: authority interrupter (specific plan) ────────────────────────────


class TestAuthorityInterrupter:
    """R3 + specific_plan → provisional → AUTHORITY_CONFIRMATION_REQUIRED."""

    def _run(self):
        return run_setback_module(
            project_inputs=_standard_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
            specific_plan="Exposition Corridor Specific Plan",
        )

    def test_coverage_partial(self):
        assert self._run().coverage_level == CoverageLevel.PARTIAL

    def test_run_status_partial(self):
        assert self._run().run_status == RunStatus.PARTIAL

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_confidence_medium(self):
        # provisional → MEDIUM
        assert self._run().confidence == ConfidenceLevel.MEDIUM

    def test_action_posture_authority_confirmation(self):
        assert (
            self._run().interpretation.action_posture
            == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
        )

    def test_specific_plan_in_inputs_summary(self):
        result = self._run()
        assert result.inputs_summary.get("specific_plan") == "Exposition Corridor Specific Plan"

    def test_specific_plan_in_authority_interrupters(self):
        payload = self._run().module_payload
        assert "specific_plan" in payload["authority_interrupters"]

    def test_baseline_yard_family_still_set(self):
        # Interrupter detected but baseline family was resolved
        payload = self._run().module_payload
        assert payload["baseline_yard_family"] == "R3"


# ── COMPLETE: clean confirmed (rear/side only, no overlays) ───────────────────


class TestCleanConfirmed:
    """R3, no overlays, no front edges → COMPLETE, OK, CAN_RELY_WITH_REVIEW.

    Front edges always produce status='provisional' (prevailing setback not
    calculated). This test uses only rear + side edges to reach 'confirmed'.
    """

    def _run(self):
        return run_setback_module(
            project_inputs=_rear_side_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )

    def test_coverage_complete(self):
        assert self._run().coverage_level == CoverageLevel.COMPLETE

    def test_run_status_ok(self):
        assert self._run().run_status == RunStatus.OK

    def test_blocking_false(self):
        assert self._run().blocking is False

    def test_confidence_high(self):
        assert self._run().confidence == ConfidenceLevel.HIGH

    def test_action_posture_can_rely(self):
        assert (
            self._run().interpretation.action_posture == ActionPosture.CAN_RELY_WITH_REVIEW
        )

    def test_governing_yard_family_set(self):
        assert self._run().module_payload["governing_yard_family"] == "R3"

    def test_baseline_equals_governing(self):
        payload = self._run().module_payload
        assert payload["baseline_yard_family"] == payload["governing_yard_family"]

    def test_plain_language_lists_edges(self):
        plr = self._run().interpretation.plain_language_result
        # Each edge_id should appear in the result
        for edge_id in ("E_rear", "E_side1", "E_side2"):
            assert edge_id in plr

    def test_plain_language_shows_governing_family(self):
        plr = self._run().interpretation.plain_language_result
        assert "R3" in plr


# ── Per-edge payload invariants ───────────────────────────────────────────────


class TestPerEdgePayload:
    """module_payload['edges'] is a list; no single governing setback scalar."""

    def setup_method(self):
        result = run_setback_module(
            project_inputs=_rear_side_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )
        self.payload = result.module_payload

    def test_edges_is_list(self):
        assert isinstance(self.payload["edges"], list)

    def test_edges_count(self):
        assert len(self.payload["edges"]) == 3

    def test_each_edge_has_required_keys(self):
        for edge in self.payload["edges"]:
            for key in (
                "edge_id",
                "classification",
                "baseline_yard_ft",
                "governing_yard_ft",
                "status",
                "prevailing_setback_flag",
                "manual_review_reasons",
            ):
                assert key in edge, f"Edge missing key: {key}"

    def test_no_governing_setback_scalar(self):
        # The adapter must NOT collapse edges to a single number
        assert "governing_setback_ft" not in self.payload
        assert "governing_yard_ft" not in self.payload  # only inside edges dicts

    def test_baseline_and_governing_family_both_present(self):
        assert "baseline_yard_family" in self.payload
        assert "governing_yard_family" in self.payload

    def test_full_output_is_dict(self):
        assert isinstance(self.payload["full_output"], dict)


# ── Module payload structure ──────────────────────────────────────────────────


class TestModulePayloadStructure:
    """Top-level module_payload keys are present."""

    def setup_method(self):
        self.payload = run_setback_module(
            project_inputs=_standard_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        ).module_payload

    def test_required_top_level_keys(self):
        for key in (
            "overall_status",
            "baseline_yard_family",
            "governing_yard_family",
            "cm_split",
            "ras_split",
            "authority_interrupters",
            "edges",
            "full_output",
        ):
            assert key in self.payload, f"Missing key: {key}"

    def test_authority_interrupters_is_list(self):
        assert isinstance(self.payload["authority_interrupters"], list)

    def test_cm_split_is_bool(self):
        assert isinstance(self.payload["cm_split"], bool)


# ── Module result schema ──────────────────────────────────────────────────────


class TestModuleResultSchema:
    """ModuleResult envelope contract."""

    def _run(self):
        return run_setback_module(
            project_inputs=_empty_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )

    def test_is_module_result(self):
        assert isinstance(self._run(), ModuleResult)

    def test_module_name_is_setback(self):
        assert self._run().module == "setback"

    def test_module_version_default(self):
        assert self._run().module_version == "v1"

    def test_inputs_summary_has_base_zone(self):
        assert self._run().inputs_summary["base_zone"] == "R3"

    def test_inputs_summary_has_raw_zone(self):
        assert self._run().inputs_summary["raw_zone"] == "R3-1"

    def test_inputs_summary_has_number_of_edges(self):
        assert self._run().inputs_summary["number_of_edges"] == 0

    def test_provenance_populated(self):
        # THIN case still runs authority step — zone table is a source
        result = run_setback_module(
            project_inputs=_rear_side_inputs(),
            raw_zone="R3-1",
            base_zone="R3",
        )
        assert len(result.provenance.authoritative_sources_used) > 0
