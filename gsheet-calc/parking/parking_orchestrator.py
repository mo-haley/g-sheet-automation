"""Parking module orchestrator.

Thin orchestrator that calls each parking step in sequence.
Density output feeds parking input.

This orchestrator computes plausible lane-module results for comparison
and final assembly. It does NOT select the operative lane — that happens
in parking_status.select_parking_lane().
"""

from __future__ import annotations

from density.models import DensityOutput
from models.project import Project
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
