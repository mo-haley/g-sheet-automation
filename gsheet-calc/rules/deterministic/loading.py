"""Loading space calculation rule per LAMC 12.21 C.6."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_loading_data() -> dict:
    path = DATA_DIR / "loading_rules.json"
    return json.loads(path.read_text())


class LoadingRule(BaseRule):
    """Determine loading space requirement."""

    authority_id = "AUTH-LOAD"
    code_section = "LAMC 12.21 C.6"
    topic = "Loading space"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        data = _load_loading_data()

        loading = data.get("loading", {})
        required_zones = loading.get("required_zones", [])
        min_area = loading.get("min_area_sf", 400)
        max_exempt = loading.get("exemption", {}).get("max_exempt_units", 29)

        zone = site.zone or ""
        total_units = project.total_units
        alley_adjacent = project.alley_adjacent

        steps = []
        required = False

        if zone not in required_zones:
            steps.append(f"Zone {zone} not in required zones {required_zones}: loading not required by zone")
        elif total_units <= max_exempt:
            steps.append(f"Units ({total_units}) <= {max_exempt}: exempt (apartment <30 units)")
        elif not alley_adjacent:
            steps.append("Lot does not abut an improved alley: loading not required")
        else:
            required = True
            steps.append(f"Zone {zone} in required zones, {total_units} units > {max_exempt}, alley-adjacent")
            steps.append(f"Loading space required: min {min_area} sf")

        results.append(self._make_result(
            "loading_required",
            required,
            unit="",
            formula="zone in required_zones AND units > 29 AND alley_adjacent",
            inputs_used={
                "zone": zone,
                "total_units": total_units,
                "alley_adjacent": alley_adjacent,
            },
            intermediate_steps=steps,
        ))

        if required:
            results.append(self._make_result(
                "loading_min_area",
                min_area,
                unit="sf",
                formula=f"Min {min_area} sf per LAMC 12.21 C.6",
                inputs_used={},
            ))

        return results, issues
