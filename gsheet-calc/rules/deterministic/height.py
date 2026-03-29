"""Height and story limit calculation rule."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_hd_table() -> dict:
    path = DATA_DIR / "height_districts.json"
    return json.loads(path.read_text())


class HeightRule(BaseRule):
    """Determine baseline height and story limits from height district."""

    authority_id = "AUTH-HEIGHT"
    code_section = "LAMC 12.21.1"
    topic = "Height and story limits"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []

        hd_data = _load_hd_table()
        hd_info = hd_data.get("height_districts", {}).get(site.height_district or "", {})

        if not hd_info:
            issues.append(
                ReviewIssue(
                    id="CALC-HT-001",
                    category="height",
                    severity="high",
                    title=f"Height district '{site.height_district}' not found",
                    description="Cannot determine height/story limits.",
                    affected_fields=["height_limit_ft", "story_limit"],
                    suggested_review_role="zoning consultant",
                    blocking=True,
                )
            )
            return results, issues

        # Zone-class-specific lookup (preferred) with fallback to global (deprecated)
        zone_class_map = hd_data.get("zone_class_map", {})
        zone_class_key = zone_class_map.get(site.zone or "")
        hs_by_class = hd_info.get("height_story_by_zone_class", {})
        hs_entry = hs_by_class.get(zone_class_key or "", {}) if zone_class_key else {}

        if hs_entry:
            height_limit = hs_entry.get("height_ft")
            story_limit = hs_entry.get("stories")
        else:
            # Fallback to deprecated global values
            height_limit = hd_info.get("height_limit_ft")
            story_limit = hd_info.get("story_limit")
            if zone_class_key:
                issues.append(
                    ReviewIssue(
                        id="CALC-HT-003",
                        category="height",
                        severity="medium",
                        title=f"No zone-specific height/story data for '{zone_class_key}' in HD {site.height_district}",
                        description="Using deprecated global HD limits. Zone-specific limits may differ.",
                        affected_fields=["height_limit_ft", "story_limit"],
                        suggested_review_role="zoning consultant",
                    )
                )

        review_notes = []
        if site.specific_plan:
            review_notes.append(
                f"Site is in specific plan '{site.specific_plan}' which may override HD limits."
            )
            issues.append(
                ReviewIssue(
                    id="CALC-HT-002",
                    category="height",
                    severity="medium",
                    title="Specific plan may modify height limits",
                    description=f"Specific plan '{site.specific_plan}' detected. HD limits shown are baseline only.",
                    affected_fields=["height_limit_ft", "story_limit"],
                    suggested_review_role="planner",
                )
            )

        if site.hillside_area:
            review_notes.append("Hillside area: additional height restrictions may apply.")

        results.append(self._make_result(
            "height_limit_ft",
            height_limit,
            unit="ft",
            formula=f"HD {site.height_district} height limit",
            inputs_used={"height_district": site.height_district},
            review_notes=review_notes,
            confidence="medium" if review_notes else "high",
            assumptions=["No height limit from HD" if height_limit is None else f"HD limit: {height_limit} ft"],
        ))

        results.append(self._make_result(
            "story_limit",
            story_limit,
            unit="stories",
            formula=f"HD {site.height_district} story limit",
            inputs_used={"height_district": site.height_district},
            review_notes=review_notes,
            confidence="medium" if review_notes else "high",
        ))

        return results, issues
