"""Adaptive Reuse Ordinance stub screen."""

from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def screen_adaptive_reuse(site: Site, project: Project) -> ScenarioResult:
    """Stub screen for Adaptive Reuse Ordinance.

    Not fully implemented in V1. Returns an unresolved advisory result.
    """
    if not project.adaptive_reuse:
        return ScenarioResult(
            name="Adaptive Reuse Ordinance",
            status="likely_ineligible",
            determinism="advisory",
            summary="Project not flagged as adaptive reuse.",
        )

    return ScenarioResult(
        name="Adaptive Reuse Ordinance",
        status="unresolved",
        determinism="advisory",
        summary="Adaptive reuse flagged but not fully screened in V1.",
        unresolved=["Full ARO eligibility screening not implemented in V1."],
        process_notes=[
            "ARO may waive certain zoning requirements for conversion of existing buildings.",
            "Requires manual review by planner.",
        ],
    )
