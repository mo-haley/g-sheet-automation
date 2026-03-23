"""EV parking requirement rules per 2025 CALGreen."""

import json
import math

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_ev_data() -> dict:
    path = DATA_DIR / "ev_requirements.json"
    return json.loads(path.read_text())


class EVParkingRule(BaseRule):
    """Calculate EV parking/charging requirements for multifamily projects."""

    authority_id = "AUTH-EV-MF"
    code_section = "CALGreen 4.106.4.2.2"
    topic = "EV parking requirements"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        data = _load_ev_data()

        mf = data.get("multifamily", {})
        total_units = project.total_units
        assigned = project.parking_assigned
        unassigned = project.parking_unassigned
        total_spaces = project.parking_spaces_total

        steps = []

        # Check if assigned/unassigned split is known
        if assigned is None or unassigned is None:
            if total_spaces is not None:
                issues.append(
                    ReviewIssue(
                        id="CALC-EV-001",
                        category="ev_parking",
                        severity="medium",
                        title="Assigned/unassigned parking split not provided",
                        description=(
                            "EV requirements differ for assigned vs unassigned spaces. "
                            "Calculating based on total spaces with assumptions."
                        ),
                        affected_fields=["ev_receptacles", "ev_evse"],
                        suggested_review_role="architect",
                    )
                )
                # Default assumption: 1 assigned per unit, rest unassigned
                assigned = min(total_units, total_spaces)
                unassigned = max(0, total_spaces - assigned)
                steps.append(f"Assumed split: {assigned} assigned, {unassigned} unassigned")
            else:
                issues.append(
                    ReviewIssue(
                        id="CALC-EV-002",
                        category="ev_parking",
                        severity="high",
                        title="No parking count for EV calculation",
                        description="Cannot calculate EV requirements without total parking spaces.",
                        affected_fields=["ev_receptacles", "ev_evse"],
                        suggested_review_role="architect",
                        blocking=True,
                    )
                )
                return results, issues

        # Assigned: 1 receptacle per unit at assigned space
        assigned_receptacles = min(total_units, assigned)
        steps.append(f"Assigned receptacles: min({total_units} units, {assigned} assigned) = {assigned_receptacles}")

        # Unassigned: 1 per unit + 25% of remaining common with installed EVSE
        unassigned_receptacles = min(total_units, unassigned)
        remaining_common = max(0, unassigned - unassigned_receptacles)
        common_evse = math.ceil(remaining_common * 0.25)
        steps.append(f"Unassigned receptacles: min({total_units}, {unassigned}) = {unassigned_receptacles}")
        steps.append(f"Remaining common: {remaining_common}, 25% EVSE = {common_evse}")

        total_receptacles = assigned_receptacles + unassigned_receptacles
        total_evse = common_evse

        results.append(self._make_result(
            "ev_receptacles",
            total_receptacles,
            unit="receptacles",
            formula="assigned: 1/unit + unassigned: 1/unit",
            inputs_used={
                "total_units": total_units,
                "parking_assigned": assigned,
                "parking_unassigned": unassigned,
            },
            intermediate_steps=steps,
        ))

        results.append(self._make_result(
            "ev_evse_installed",
            total_evse,
            unit="EVSE stations",
            formula="ceil(remaining_common * 0.25)",
            inputs_used={"remaining_common_spaces": remaining_common},
            intermediate_steps=[f"25% of {remaining_common} remaining common = {total_evse}"],
        ))

        return results, issues
