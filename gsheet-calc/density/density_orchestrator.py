"""Density module orchestrator.

Thin orchestrator that calls each density step in sequence.
"""

from __future__ import annotations

from density.density_authority import (
    check_authority_interrupters,
    establish_parcel_regime,
    map_zone_to_density_standard,
)
from density.density_baseline_calc import compute_baseline_density
from density.density_state_db_calc import compute_state_db_density
from density.density_status import assemble_density_result, run_eligibility_checks
from density.density_toc_calc import compute_toc_density
from density.models import DensityOutput, IncentiveLane
from models.project import Project
from models.site import Site


def run_density(
    site: Site,
    project: Project,
    incentive_lane: str = "unresolved",
    ed1_pathway: bool = False,
    lane_selected_by: str = "unresolved",
) -> DensityOutput:
    """Execute the full density decision sequence.

    Args:
        site: Parsed site data.
        project: Project assumptions.
        incentive_lane: One of "none", "toc", "state_db", "unresolved".
        ed1_pathway: Whether ED1 processing pathway is claimed.
        lane_selected_by: "user" or "auto" or "unresolved".
    """
    output = DensityOutput()

    # STEP 1 - Establish parcel regime
    output.parcel_regime = establish_parcel_regime(site)

    # STEP 1.5 - Map zone to density standard
    output.density_standard, std_issues = map_zone_to_density_standard(output.parcel_regime)

    # STEP 2 - Check authority interrupters
    output.authority_interrupters = check_authority_interrupters(
        output.parcel_regime, output.density_standard
    )

    # STEP 3 - Compute baseline density
    output.baseline_density = compute_baseline_density(
        site, output.parcel_regime, output.authority_interrupters
    )

    # STEP 4 - Record incentive lane selection
    output.incentive_lane = IncentiveLane(
        selected=incentive_lane,
        ed1_pathway=ed1_pathway,
        selected_by=lane_selected_by,
        confidence="confirmed" if incentive_lane in ("none", "toc", "state_db") and lane_selected_by == "user" else "unresolved" if incentive_lane == "unresolved" else "provisional",
    )

    # STEP 5a - TOC density (if lane = toc)
    if incentive_lane == "toc":
        output.toc_density = compute_toc_density(
            output.parcel_regime, output.baseline_density
        )

    # STEP 5b - State DB density (if lane = state_db)
    if incentive_lane == "state_db":
        output.state_db_density = compute_state_db_density(
            site, output.parcel_regime, output.authority_interrupters,
            output.baseline_density, project,
        )

    # STEP 6 - Eligibility checks
    output.eligibility_checks = run_eligibility_checks(
        project, output.incentive_lane, output.baseline_density
    )

    # STEP 7 - Assemble result
    output.density_result = assemble_density_result(output)

    return output
