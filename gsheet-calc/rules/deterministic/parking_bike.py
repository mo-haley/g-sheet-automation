"""Bicycle parking calculation rule."""

import json
import math

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_bike_data() -> dict:
    path = DATA_DIR / "bike_parking.json"
    return json.loads(path.read_text())


class BikeParkingRule(BaseRule):
    """Calculate required bicycle parking spaces."""

    authority_id = "AUTH-BIKE-RES"
    code_section = "LAMC 12.21 A.16"
    topic = "Bicycle parking"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        data = _load_bike_data()

        res_data = data.get("residential", {})
        threshold = res_data.get("threshold_units", 3)
        total_units = project.total_units

        # Residential bike parking
        long_term = 0
        short_term = 0
        steps = []

        if total_units > threshold:
            long_term = total_units * res_data.get("long_term_per_unit", 1)
            short_term_raw = total_units / res_data.get("short_term_per_units", 10)
            short_term = max(res_data.get("short_term_minimum", 2), math.ceil(short_term_raw))
            steps = [
                f"Units: {total_units} (> {threshold} threshold)",
                f"Long-term: {total_units} x 1 = {long_term}",
                f"Short-term: ceil({total_units} / 10) = {math.ceil(short_term_raw)}, min 2 -> {short_term}",
            ]
        else:
            steps = [f"Units: {total_units} (<= {threshold} threshold, no bike parking required)"]

        results.append(self._make_result(
            "bike_parking_long_term",
            long_term,
            unit="spaces",
            formula=f"{total_units} x 1 per unit",
            inputs_used={"total_units": total_units},
            intermediate_steps=steps,
        ))

        results.append(self._make_result(
            "bike_parking_short_term",
            short_term,
            unit="spaces",
            formula=f"max(2, ceil({total_units} / 10))",
            inputs_used={"total_units": total_units},
        ))

        # Note auto replacement allowance
        results.append(self._make_result(
            "bike_auto_replacement_note",
            "See LAMC 12.21 A.16(d)",
            unit="",
            authority_id="AUTH-BIKE-REPLACE",
            determinism="advisory",
            confidence="medium",
            review_notes=["Bike parking may substitute for a portion of auto parking. Not auto-applied."],
        ))

        return results, issues
