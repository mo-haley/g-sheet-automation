"""Transit Oriented Communities (TOC) eligibility screen."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def _load_screen_data() -> dict:
    path = DATA_DIR / "screen_thresholds.json"
    return json.loads(path.read_text())


def screen_toc(site: Site, project: Project) -> ScenarioResult:
    """Screen for TOC eligibility based on site TOC tier."""
    data = _load_screen_data()
    toc_data = data.get("toc", {})
    tiers = toc_data.get("tiers", {})
    issues: list[ReviewIssue] = []
    missing: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []
    parking_notes: list[str] = []

    if site.toc_tier is None:
        missing.append("toc_tier")
        return ScenarioResult(
            name="TOC Incentive Program",
            status="unresolved",
            summary="TOC tier not available from ZIMAS. Cannot screen eligibility.",
            missing_inputs=missing,
        )

    tier_key = str(site.toc_tier)
    tier_info = tiers.get(tier_key)

    if not tier_info:
        return ScenarioResult(
            name="TOC Incentive Program",
            status="likely_ineligible",
            summary=f"TOC tier {site.toc_tier} not recognized or site not in TOC area.",
        )

    # Check for specific plan conflicts
    if site.specific_plan:
        unresolved.append(
            f"Specific plan '{site.specific_plan}' may conflict with TOC incentives."
        )
        issues.append(
            ReviewIssue(
                id="ADV-TOC-001",
                category="advisory",
                severity="high",
                title="TOC / specific plan conflict",
                description=(
                    f"Site is in specific plan '{site.specific_plan}' and TOC tier {site.toc_tier}. "
                    "TOC incentives may not apply if specific plan governs."
                ),
                affected_fields=["toc_tier"],
                suggested_review_role="planner",
            )
        )

    # Affordability check
    if project.affordability is None:
        missing.append("affordability")
        notes.append("Affordability plan not provided. TOC requires affordable set-aside.")

    density_bonus = tier_info.get("density_bonus_pct", 0)
    far_bonus = tier_info.get("far_bonus_pct", 0)
    height_bonus = tier_info.get("height_bonus_stories", 0)
    parking_reduction = tier_info.get("parking_reduction", "")
    os_reduction = tier_info.get("open_space_reduction_pct", 0)

    notes.extend([
        f"TOC Tier {site.toc_tier}:",
        f"  Density bonus: +{density_bonus}%",
        f"  FAR bonus: +{far_bonus}%",
        f"  Height bonus: +{height_bonus} stories",
        f"  Open space reduction: -{os_reduction}%",
    ])
    parking_notes.append(f"TOC parking: {parking_reduction}")

    status = "likely_eligible" if not missing and not unresolved else "unresolved"

    return ScenarioResult(
        name="TOC Incentive Program",
        status=status,
        determinism="advisory",
        summary=f"TOC Tier {site.toc_tier} screening. Advisory only.",
        eligibility_notes=notes,
        missing_inputs=missing,
        unresolved=unresolved,
        indicative_parking_notes=parking_notes,
        issues=issues,
    )
