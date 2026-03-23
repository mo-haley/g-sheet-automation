"""Parking module orchestrator.

Thin orchestrator that calls each parking step in sequence.
Density output feeds parking input.

This orchestrator computes plausible lane-module results for comparison
and final assembly. It does NOT select the operative lane — that happens
in parking_status.select_parking_lane().

Adapter entry point:
    run_parking_module(site, project) -> ModuleResult
    run_parking_module(site, project, density_output=..., parking_lane=...) -> ModuleResult

Legacy entry point (untouched):
    run_parking(site, project, density_output, parking_lane=None) -> ParkingOutput
"""

from __future__ import annotations

from density.density_orchestrator import run_density
from density.models import DensityOutput
from models.project import Project
from models.result_common import (
    ActionPosture,
    ConfidenceLevel,
    CoverageLevel,
    Interpretation,
    ModuleResult,
    Provenance,
    RunStatus,
)
from models.site import Site
from parking.models import ParkingOutput, ParkingIssue
from parking.parking_ab2097 import check_ab2097
from parking.parking_authority import check_parking_interrupters, identify_code_family
from parking.parking_baseline_calc import compute_baseline_parking
from parking.parking_state_db import compute_state_db_parking
from parking.parking_status import (
    assemble_parking_result,
    compute_parking_comparison,
    select_parking_lane,
)
from parking.parking_toc import compute_toc_parking


def _derive_100_pct_affordable(
    project: Project,
    density_output: DensityOutput,
) -> tuple[bool, bool]:
    """Derive 100% affordable signals from the broadest available basis.

    Sources checked (in priority order):
      1. Density State DB output (if it exists and evaluated affordability)
      2. Project affordability plan directly

    Returns:
        (is_100_pct_affordable, is_100_pct_affordable_confirmed)
    """
    # Source 1: density State DB branch (most authoritative if it ran)
    if density_output.state_db_density and density_output.state_db_density.is_100_pct_affordable:
        return True, density_output.state_db_density.is_100_pct_affordable_confirmed

    # Source 2: project affordability plan directly
    if project.affordability:
        total_aff = (
            project.affordability.eli_pct + project.affordability.vli_pct +
            project.affordability.li_pct + project.affordability.moderate_pct
        )
        if total_aff >= 100.0:
            # Project data indicates 100% affordable, but this is project intent
            # not confirmed treatment — return planned=True, confirmed=False
            return True, False

    return False, False


def _resolve_unit_count(
    project: Project,
    density_output: DensityOutput,
) -> tuple[int, str]:
    """Resolve unit count for parking computation and document the basis.

    Returns:
        (total_units, unit_count_basis)
    """
    if density_output.density_result.claimed_density_is_unlimited:
        return project.total_units, "proposed_units_unlimited_density"

    claimed = density_output.density_result.claimed_density_units
    if claimed is not None:
        return claimed, "density_claimed_units"

    baseline = density_output.density_result.baseline_units_before_incentives
    if baseline is not None:
        return baseline, "density_baseline_units"

    return project.total_units, "project_total_units_fallback"


def run_parking(
    site: Site,
    project: Project,
    density_output: DensityOutput,
    parking_lane: str | None = None,
) -> ParkingOutput:
    """Execute the full parking decision sequence.

    Args:
        site: Parsed site data.
        project: Project assumptions.
        density_output: Output from density module (required dependency).
        parking_lane: Optional user-selected parking lane override.
    """
    output = ParkingOutput()
    orchestrator_issues: list[ParkingIssue] = []

    # STEP 1 - Identify code family
    output.code_family = identify_code_family(site)

    # STEP 1.5 - AB 2097 threshold gate
    output.ab2097 = check_ab2097(site, project)

    # STEP 2 - Compute baseline local parking
    output.baseline_parking = compute_baseline_parking(project)

    # STEP 3 - Check parking authority interrupters
    output.parking_interrupters = check_parking_interrupters(site, output.code_family)

    # ── Resolve shared inputs for lane modules ──────────────────────
    density_lane = density_output.incentive_lane.selected
    total_units, unit_count_basis = _resolve_unit_count(project, density_output)

    # Make the unit-count assumption basis visible
    if unit_count_basis == "proposed_units_unlimited_density":
        orchestrator_issues.append(ParkingIssue(
            step="parking_orchestrator",
            field="total_units",
            severity="info",
            message=(
                f"Density treated as unlimited. Parking uses project's proposed "
                f"unit count ({total_units}) rather than a density-capped baseline. "
                f"This is an assumption basis — actual parking depends on final "
                f"approved unit count."
            ),
            confidence_impact="none",
        ))
    elif unit_count_basis == "project_total_units_fallback":
        orchestrator_issues.append(ParkingIssue(
            step="parking_orchestrator",
            field="total_units",
            severity="warning",
            message=(
                f"Neither density claimed units nor baseline units available. "
                f"Using project.total_units ({total_units}) as fallback."
            ),
            action_required="Confirm unit count for parking calculation.",
            confidence_impact="degrades_to_provisional",
        ))

    # Derive 100% affordable from broadest available basis
    is_100_aff, is_100_aff_confirmed = _derive_100_pct_affordable(project, density_output)

    toc_tier = density_output.parcel_regime.toc_tier_zimas
    toc_verified = density_output.parcel_regime.toc_tier_verified
    gating = output.parking_interrupters.lane_gating

    # ── STEP 5a - TOC parking ───────────────────────────────────────
    # Compute when: user selected, density lane aligned, OR plausibly in play
    toc_should_compute = (
        density_lane == "toc"
        or parking_lane == "toc"
        or gating.toc_plausible is True
    )
    if toc_should_compute and toc_tier is not None:
        output.toc_parking = compute_toc_parking(
            tier=toc_tier,
            total_units=total_units,
            tier_verified=toc_verified,
            is_100_pct_affordable=is_100_aff,
            is_100_pct_affordable_confirmed=is_100_aff_confirmed,
        )

    # ── STEP 5b - State DB parking ──────────────────────────────────
    # Compute when: user selected, density lane aligned, OR plausibly in play
    state_db_should_compute = (
        density_lane == "state_db"
        or parking_lane == "state_db"
        or gating.state_db_plausible is True
    )
    if state_db_should_compute:
        output.state_db_parking = compute_state_db_parking(
            project=project,
            total_units=total_units,
            is_100_pct_affordable=is_100_aff,
            is_100_pct_affordable_confirmed=is_100_aff_confirmed,
        )

    # STEP 4 - Select operative reduction lane
    output.parking_lane = select_parking_lane(
        ab2097=output.ab2097,
        baseline=output.baseline_parking,
        toc_parking=output.toc_parking,
        state_db_parking=output.state_db_parking,
        density_lane=density_lane,
        interrupters=output.parking_interrupters,
        user_selected_lane=parking_lane,
    )

    # STEP 6 - Compare legal minimum to proposed
    output.parking_comparison = compute_parking_comparison(
        governing_minimum=output.parking_lane.governing_minimum,
        proposed_parking=project.parking_spaces_total,
    )

    # STEP 7 - Assemble result (pass orchestrator issues for collection)
    density_status = density_output.density_result.status
    output.parking_result = assemble_parking_result(
        output,
        density_status=density_status,
        orchestrator_issues=orchestrator_issues,
    )

    return output


# ── ModuleResult adapter ───────────────────────────────────────────────────────


def _map_coverage_level(output: ParkingOutput) -> CoverageLevel:
    """Map ParkingOutput to CoverageLevel.

    PARTIAL when any of:
      - flagged_controls is non-empty (SP/CPIO/D/Q detected but not reviewed)
      - parking lane is unresolved (no governing minimum can be stated)
      - used_default_unit_mix_assumption (unit type assumption degraded result)
    COMPLETE otherwise.

    THIN/UNCERTAIN are not used for parking — the LAMC 12.21 A.4 baseline is
    always computable from project data regardless of site inputs.
    """
    if output.parking_interrupters.flagged_controls:
        return CoverageLevel.PARTIAL
    if output.parking_lane.selected == "unresolved":
        return CoverageLevel.PARTIAL
    if output.baseline_parking.used_default_unit_mix_assumption:
        return CoverageLevel.PARTIAL
    return CoverageLevel.COMPLETE


def _map_run_status(coverage: CoverageLevel) -> RunStatus:
    """Run status is always PARTIAL — prior_case_conditions is unchecked."""
    # prior_case_conditions=None (never checked) keeps this provisional.
    # OK is aspirational until that check is added.
    return RunStatus.PARTIAL


def _map_confidence(output: ParkingOutput) -> ConfidenceLevel:
    status = output.parking_result.status
    if status == "confirmed":
        return ConfidenceLevel.HIGH
    elif status in ("provisional", "overridden"):
        return ConfidenceLevel.MEDIUM
    else:  # unresolved / conflict
        return ConfidenceLevel.LOW


def _map_action_posture(output: ParkingOutput) -> ActionPosture:
    """Priority: flagged_controls → AUTHORITY_CONFIRMATION; unresolved lane → ACT_ON_DETECTED; else → CAN_RELY."""
    if output.parking_interrupters.flagged_controls:
        return ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
    if output.parking_lane.selected == "unresolved":
        return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    return ActionPosture.CAN_RELY_WITH_REVIEW


def _build_plain_language_result(output: ParkingOutput) -> str:
    """Three cases — baseline known / reduction lane unresolved / governing minimum stated."""
    baseline = output.baseline_parking.total_baseline
    lane = output.parking_lane.selected
    baseline_str = f"Baseline: {baseline:.1f} spaces (LAMC 12.21 A.4)."

    if lane == "none":
        return f"{baseline_str} No reduction lane applicable; baseline governs."

    if lane == "unresolved":
        gating = output.parking_interrupters.lane_gating
        plausible = []
        if gating.ab2097_plausible is True:
            plausible.append("AB 2097")
        if gating.toc_plausible is True:
            plausible.append("TOC")
        if gating.state_db_plausible is True:
            plausible.append("State DB")
        plausible_str = ", ".join(plausible) if plausible else "no lanes identified"
        return (
            f"{baseline_str} Parking reduction lane unresolved — {plausible_str} plausible; "
            f"no governing minimum stated until lane is selected."
        )

    # Specific lane resolved
    governing = output.parking_lane.governing_minimum
    proposed = output.parking_comparison.proposed_parking
    delta = output.parking_comparison.delta
    lane_label = lane.upper() if lane in ("toc", "ab2097") else lane.replace("_", " ").title()

    if governing is not None:
        governing_str = f"Governing ({lane_label}): {governing:.1f} spaces."
    else:
        governing_str = f"Governing ({lane_label}): not stated."

    parts = [baseline_str, governing_str]
    if proposed is not None and delta is not None:
        sign = "+" if delta >= 0 else ""
        direction = "above" if delta >= 0 else "below"
        parts.append(f"Proposed: {proposed} spaces ({sign}{delta:.1f} {direction} minimum).")
    elif proposed is not None:
        parts.append(f"Proposed: {proposed} spaces.")

    return " ".join(parts)


def _build_summary_str(output: ParkingOutput) -> str:
    lane = output.parking_lane.selected
    baseline = output.baseline_parking.total_baseline
    if lane == "none":
        return f"Parking: {baseline:.1f} spaces required (LAMC baseline, no reduction lane)."
    if lane == "unresolved":
        return f"Parking: reduction lane unresolved; baseline {baseline:.1f} spaces."
    governing = output.parking_lane.governing_minimum
    lane_label = lane.upper() if lane in ("toc", "ab2097") else lane.replace("_", " ").title()
    if governing is not None:
        return f"Parking: {governing:.1f} spaces governing ({lane_label} lane); baseline {baseline:.1f}."
    return f"Parking: {lane_label} lane selected; governing minimum not computed."


def _build_provenance(output: ParkingOutput) -> Provenance:
    sources = ["lamc_12_21_a4_parking_table", "zimas_parcel_data"]
    if output.toc_parking is not None:
        sources.append("toc_parking_ordinance")
    if output.state_db_parking is not None:
        sources.append("govt_code_65915")
    return Provenance(authoritative_sources_used=sources)


def _build_inputs_summary(site: Site, project: Project, output: ParkingOutput) -> dict:
    d: dict = {
        "base_zone": site.zone,
        "lot_area_sf": site.lot_area_sf,
        "total_units": project.total_units,
        "proposed_parking": project.parking_spaces_total,
        "active_lane": output.parking_lane.selected,
    }
    if site.specific_plan:
        d["specific_plan"] = site.specific_plan
    if site.overlay_zones:
        d["overlay_zones"] = site.overlay_zones
    if output.parking_interrupters.flagged_controls:
        d["flagged_controls"] = [
            fc.identifier for fc in output.parking_interrupters.flagged_controls
        ]
    return d


def _build_module_payload(output: ParkingOutput) -> dict:
    lane_results: dict = {}
    if output.ab2097 is not None:
        lane_results["ab2097"] = {
            "eligible": output.ab2097.eligible,
            "confidence": output.ab2097.confidence,
            "result": output.parking_lane.ab2097_result,
        }
    if output.toc_parking is not None:
        lane_results["toc"] = {
            "tier": output.toc_parking.tier,
            "required_spaces": output.toc_parking.required_spaces,
            "status": output.toc_parking.status,
            "result": output.parking_lane.toc_result,
        }
    if output.state_db_parking is not None:
        lane_results["state_db"] = {
            "total_required": output.state_db_parking.total_required,
            "status": output.state_db_parking.status,
            "result": output.parking_lane.state_db_result,
        }

    return {
        "baseline_required": output.baseline_parking.total_baseline,
        "governing_required": output.parking_lane.governing_minimum,
        "active_lane": output.parking_lane.selected,
        "lane_results": lane_results,
        "proposed_parking": output.parking_comparison.proposed_parking,
        "parking_delta": output.parking_comparison.delta,
        "full_output": output.model_dump(),
    }


def run_parking_module(
    site: Site,
    project: Project,
    density_output: DensityOutput | None = None,
    parking_lane: str | None = None,
) -> ModuleResult:
    """Run parking pipeline and return a standardized ModuleResult.

    If density_output is not provided, density is run internally via
    run_density(incentive_lane="none") — baseline-only, no incentive selection.
    Pass density_output explicitly when the caller already has it.
    """
    if density_output is None:
        density_output = run_density(site, project, incentive_lane="none", lane_selected_by="auto")

    output = run_parking(site, project, density_output, parking_lane=parking_lane)

    coverage = _map_coverage_level(output)
    run_status = _map_run_status(coverage)
    confidence = _map_confidence(output)
    action_posture = _map_action_posture(output)

    return ModuleResult(
        module="parking",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=False,
        inputs_summary=_build_inputs_summary(site, project, output),
        interpretation=Interpretation(
            summary=_build_summary_str(output),
            plain_language_result=_build_plain_language_result(output),
            action_posture=action_posture,
        ),
        provenance=_build_provenance(output),
        module_payload=_build_module_payload(output),
    )
