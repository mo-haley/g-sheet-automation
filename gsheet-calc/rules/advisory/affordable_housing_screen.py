"""100% Affordable housing streamlining screen (AB 2334 / AB 1763)."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def _load_screen_data() -> dict:
    path = DATA_DIR / "screen_thresholds.json"
    return json.loads(path.read_text())


def screen_100pct_affordable(site: Site, project: Project) -> ScenarioResult:
    """Screen for 100% affordable housing streamlining eligibility."""
    data = _load_screen_data()
    aff_data = data.get("affordable_100pct", {})
    issues: list[ReviewIssue] = []
    missing: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []

    # Check affordability
    if project.affordability is None:
        missing.append("affordability")
    else:
        aff = project.affordability
        total_affordable = aff.eli_pct + aff.vli_pct + aff.li_pct + aff.moderate_pct
        if total_affordable < 100:
            notes.append(
                f"Total affordable set-aside: {total_affordable}% (requires 100% excluding manager units)"
            )
            return ScenarioResult(
                name="100% Affordable Streamlining",
                status="likely_ineligible",
                determinism="advisory",
                summary="Project is not 100% affordable.",
                eligibility_notes=notes,
            )

    # Check transit/VMT
    missing_flags = aff_data.get("missing_input_flags", [])
    for flag in missing_flags:
        missing.append(flag)

    if site.ab2097_area is None:
        missing.append("ab2097_area (transit proximity)")
    elif not site.ab2097_area:
        unresolved.append("Site may not be within 0.5 mile of major transit stop.")

    notes.extend([
        "AB 2334 / AB 1763 screening:",
        "  Potential benefits: unlimited density, height, concessions",
        "  Requires: 100% affordable, low VMT area, transit proximity",
    ])

    if missing:
        issues.append(
            ReviewIssue(
                id="ADV-AFF100-001",
                category="advisory",
                severity="medium",
                title="Missing inputs for 100% affordable screening",
                description=f"Cannot fully screen without: {', '.join(missing)}",
                affected_fields=missing,
                suggested_review_role="planner",
            )
        )

    status = "unresolved" if missing or unresolved else "likely_eligible"

    return ScenarioResult(
        name="100% Affordable Streamlining",
        status=status,
        determinism="advisory",
        summary="100% affordable housing streamlining (AB 2334/1763) advisory screen.",
        eligibility_notes=notes,
        missing_inputs=missing,
        unresolved=unresolved,
        issues=issues,
    )
