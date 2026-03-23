"""State Density Bonus Law eligibility screen."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def _load_screen_data() -> dict:
    path = DATA_DIR / "screen_thresholds.json"
    return json.loads(path.read_text())


def screen_density_bonus(site: Site, project: Project) -> ScenarioResult:
    """Screen for State Density Bonus eligibility at multiple set-aside levels."""
    data = _load_screen_data()
    db_data = data.get("density_bonus", {})
    set_asides = db_data.get("set_asides", {})
    issues: list[ReviewIssue] = []
    missing: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []
    yield_notes: list[str] = []
    parking_notes: list[str] = []
    labor_notes: list[str] = []

    if project.affordability is None:
        missing.append("affordability")
        notes.append("No affordability plan provided. Cannot determine bonus level.")

    # Show sliding scale at multiple levels
    notes.append("State Density Bonus sliding scales:")
    for category, params in set_asides.items():
        label = category.replace("_", " ").title()
        base_pct = params.get("base_pct", 0)
        base_bonus = params.get("base_bonus_pct", 0)
        max_sa = params.get("max_set_aside_pct", 0)
        max_bonus = params.get("max_bonus_pct", 0)
        notes.append(
            f"  {label}: {base_pct}% set-aside -> {base_bonus}% bonus, "
            f"up to {max_sa}% set-aside -> {max_bonus}% bonus"
        )

    # Calculate indicative bonus if affordability provided
    if project.affordability:
        aff = project.affordability
        if aff.vli_pct > 0:
            vli_params = set_asides.get("very_low_income", {})
            base = vli_params.get("base_pct", 5)
            base_bonus = vli_params.get("base_bonus_pct", 20)
            incr = vli_params.get("incremental_pct_per_1pct", 2.5)
            max_bonus = vli_params.get("max_bonus_pct", 50)
            extra = max(0, aff.vli_pct - base) * incr
            bonus = min(base_bonus + extra, max_bonus)
            yield_notes.append(f"VLI {aff.vli_pct}%: indicative bonus ~{bonus:.0f}%")

        if aff.li_pct > 0:
            li_params = set_asides.get("low_income", {})
            base = li_params.get("base_pct", 10)
            base_bonus = li_params.get("base_bonus_pct", 20)
            incr = li_params.get("incremental_pct_per_1pct", 1.5)
            max_bonus = li_params.get("max_bonus_pct", 50)
            extra = max(0, aff.li_pct - base) * incr
            bonus = min(base_bonus + extra, max_bonus)
            yield_notes.append(f"LI {aff.li_pct}%: indicative bonus ~{bonus:.0f}%")

    # SB 92 flag
    sb92 = db_data.get("sb92_commercial_far_cap", {})
    if project.application_date and project.application_date >= "2026-01-01":
        notes.append(f"SB 92 commercial FAR cap applies (apps after {sb92.get('effective_date', '2026-01-01')}).")

    # AB 1287 stacking
    ab1287 = db_data.get("ab1287_stacking", {})
    notes.append(f"AB 1287: {ab1287.get('notes', 'Stacking option available.')}")

    parking_notes.append("Density bonus projects may qualify for reduced parking ratios.")
    labor_notes.append("Check prevailing wage requirements for density bonus projects.")

    status = "likely_eligible" if not missing else "unresolved"

    return ScenarioResult(
        name="State Density Bonus",
        status=status,
        determinism="advisory",
        summary="State Density Bonus Law screening at various set-aside levels.",
        eligibility_notes=notes,
        missing_inputs=missing,
        unresolved=unresolved,
        indicative_yield_notes=yield_notes,
        indicative_parking_notes=parking_notes,
        labor_notes=labor_notes,
        issues=issues,
    )
