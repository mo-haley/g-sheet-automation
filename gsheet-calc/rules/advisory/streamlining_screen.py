"""Streamlining screens: SB 423 and AB 2011."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def _load_screen_data() -> dict:
    path = DATA_DIR / "screen_thresholds.json"
    return json.loads(path.read_text())


def screen_sb423(site: Site, project: Project) -> ScenarioResult:
    """Screen for SB 423 (Builder's Remedy) eligibility."""
    data = _load_screen_data()
    sb423 = data.get("streamlining", {}).get("sb423", {})
    issues: list[ReviewIssue] = []
    missing: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []

    notes.extend([
        "SB 423 Streamlining:",
        "  Eligibility depends on HCD housing element compliance determination.",
        "  Do NOT assume city compliance status — check HCD website for current determination.",
    ])

    unresolved.append(
        "HCD compliance status for LA City must be verified at time of application."
    )

    issues.append(
        ReviewIssue(
            id="ADV-SB423-001",
            category="advisory",
            severity="high",
            title="SB 423 eligibility requires HCD compliance check",
            description=(
                "SB 423 applicability depends on whether the jurisdiction has an HCD-compliant "
                "housing element. This status changes over time and must be verified."
            ),
            affected_fields=["sb423_eligibility"],
            suggested_review_role="planner",
        )
    )

    if project.affordability is None:
        missing.append("affordability (required for SB 423 set-aside)")

    process_notes = [
        "SB 423 projects receive ministerial approval (no CEQA).",
        "Labor requirements may apply.",
    ]

    return ScenarioResult(
        name="SB 423 Streamlining",
        status="unresolved",
        determinism="advisory",
        summary="SB 423 screening. Requires HCD compliance verification.",
        eligibility_notes=notes,
        missing_inputs=missing,
        unresolved=unresolved,
        process_notes=process_notes,
        issues=issues,
    )


def screen_ab2011(site: Site, project: Project) -> ScenarioResult:
    """Screen for AB 2011 commercial corridor housing eligibility."""
    data = _load_screen_data()
    ab2011 = data.get("streamlining", {}).get("ab2011", {})
    issues: list[ReviewIssue] = []
    missing: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []
    labor_notes: list[str] = []

    notes.append("AB 2011 Commercial Corridor Housing:")

    # Check commercial corridor frontage
    if project.commercial_corridor_frontage is None:
        missing.append("commercial_corridor_frontage")
    elif not project.commercial_corridor_frontage:
        notes.append("  Site does not front a qualifying commercial corridor.")
        return ScenarioResult(
            name="AB 2011 Commercial Corridor",
            status="likely_ineligible",
            determinism="advisory",
            summary="Site does not qualify: no commercial corridor frontage.",
            eligibility_notes=notes,
        )

    # Check prevailing wage
    if project.prevailing_wage_committed is None:
        missing.append("prevailing_wage_committed")
    elif not project.prevailing_wage_committed:
        notes.append("  Prevailing wage commitment required but not indicated.")
        unresolved.append("Prevailing wage commitment needed for AB 2011.")

    # Check affordability
    if project.affordability is None:
        missing.append("affordability")

    requirements = ab2011.get("requirements", [])
    for req in requirements:
        notes.append(f"  Requirement: {req}")

    labor_notes.append("AB 2011 requires prevailing wage for construction.")

    status = "unresolved" if missing or unresolved else "likely_eligible"

    return ScenarioResult(
        name="AB 2011 Commercial Corridor",
        status=status,
        determinism="advisory",
        summary="AB 2011 commercial corridor housing advisory screen.",
        eligibility_notes=notes,
        missing_inputs=missing,
        unresolved=unresolved,
        labor_notes=labor_notes,
        issues=issues,
    )
