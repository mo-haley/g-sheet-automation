"""Open space calculation rule per LAMC 12.21 G."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_os_data() -> dict:
    path = DATA_DIR / "open_space.json"
    return json.loads(path.read_text())


class OpenSpaceRule(BaseRule):
    """Calculate open space requirements for residential projects."""

    authority_id = "AUTH-OS-RES"
    code_section = "LAMC 12.21 G"
    topic = "Open space"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        data = _load_os_data()

        res = data.get("residential", {})
        threshold = res.get("threshold_units", 6)
        per_unit = res.get("per_unit", {})
        rate_small = per_unit.get("less_than_3_habitable_rooms_sf", 100)
        rate_large = per_unit.get("3_or_more_habitable_rooms_sf", 125)

        total_units = project.total_units

        if total_units < threshold:
            results.append(self._make_result(
                "open_space_required",
                0,
                unit="sf",
                formula=f"Units ({total_units}) < threshold ({threshold}): not required",
                inputs_used={"total_units": total_units, "threshold": threshold},
            ))
            return results, issues

        # Calculate per unit type
        total_os = 0.0
        steps = []

        if project.unit_mix:
            for ut in project.unit_mix:
                rate = rate_small if ut.habitable_rooms < 3 else rate_large
                os_for_type = ut.count * rate
                total_os += os_for_type
                steps.append(
                    f"{ut.label}: {ut.count} units x {rate} sf "
                    f"({'<3' if ut.habitable_rooms < 3 else '>=3'} hab rooms) = {os_for_type:.0f} sf"
                )
        else:
            # Default: assume all units have >=3 habitable rooms
            total_os = total_units * rate_large
            steps.append(f"Default: {total_units} units x {rate_large} sf = {total_os:.0f} sf")
            issues.append(
                ReviewIssue(
                    id="CALC-OS-001",
                    category="open_space",
                    severity="low",
                    title="No unit mix for open space calculation",
                    description="Using 125 sf/unit (>=3 hab rooms) for all units as default.",
                    affected_fields=["open_space_required"],
                    suggested_review_role="architect",
                )
            )

        results.append(self._make_result(
            "open_space_required",
            total_os,
            unit="sf",
            formula="sum(units * rate_per_hab_rooms)",
            inputs_used={"total_units": total_units, "unit_mix": bool(project.unit_mix)},
            intermediate_steps=steps,
        ))

        # Private credit info
        private_credit = res.get("private_credit", {})
        results.append(self._make_result(
            "open_space_private_credit_info",
            f"Up to {private_credit.get('max_credit_pct', 0.5) * 100:.0f}% may be private (min {private_credit.get('min_area_sf', 50)} sf, min {private_credit.get('min_dimension_ft', 6)}' dim)",
            unit="",
            determinism="advisory",
            confidence="high",
        ))

        if site.specific_plan:
            issues.append(
                ReviewIssue(
                    id="CALC-OS-002",
                    category="open_space",
                    severity="medium",
                    title="Specific plan may modify open space requirement",
                    description=f"Site in specific plan '{site.specific_plan}'. Open space may differ.",
                    affected_fields=["open_space_required"],
                    suggested_review_role="planner",
                )
            )

        return results, issues
