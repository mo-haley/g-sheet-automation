"""Accessible parking calculation rules per CBC Chapter 11A/11B."""

import json
import math

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_accessible_data() -> dict:
    path = DATA_DIR / "accessible_parking.json"
    return json.loads(path.read_text())


def _lookup_commercial_accessible(total_spaces: int, tiers: list[dict]) -> int:
    """Look up required accessible spaces from Table 11B-208.2."""
    for tier in tiers:
        t_min = tier["total_min"]
        t_max = tier.get("total_max")
        if t_max is not None and t_min <= total_spaces <= t_max:
            if "accessible_required" in tier:
                return tier["accessible_required"]
            if "accessible_formula" in tier:
                return math.ceil(eval(tier["accessible_formula"].replace("total", str(total_spaces))))
        elif t_max is None and total_spaces >= t_min:
            if "accessible_formula" in tier:
                return math.ceil(eval(tier["accessible_formula"].replace("total", str(total_spaces))))
    return 0


class AccessibleParkingRule(BaseRule):
    """Calculate accessible parking requirements (residential + commercial + van + EVCS)."""

    authority_id = "AUTH-ACC-COM"
    code_section = "CBC 11B-208"
    topic = "Accessible parking"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        data = _load_accessible_data()

        total_spaces = project.parking_spaces_total or 0

        # --- 6a. Residential accessible ---
        mobility_units = project.mobility_accessible_units
        res_accessible = 0
        res_steps = []

        if mobility_units is not None and mobility_units > 0:
            # 11B-208.2.3.1: 1 accessible per mobility-accessible unit
            res_accessible = mobility_units
            res_steps.append(f"Mobility-accessible units: {mobility_units}")
            res_steps.append(f"1:1 accessible spaces: {mobility_units}")

            # 11B-208.2.3.2: additional spaces beyond 1:1
            total_res_spaces = sum(
                ut.count for ut in project.unit_mix
            ) if project.unit_mix else project.total_units
            additional = max(0, total_res_spaces - mobility_units)
            if additional > 0:
                additional_accessible = max(1, math.ceil(additional * 0.02))
                res_accessible += additional_accessible
                res_steps.append(
                    f"Additional spaces beyond 1:1: {additional} x 2% = {additional_accessible}"
                )
        else:
            issues.append(
                ReviewIssue(
                    id="CALC-ACC-RES-001",
                    category="accessible_parking",
                    severity="medium",
                    title="Mobility-accessible unit count not provided",
                    description=(
                        "Cannot calculate residential accessible parking per 11B-208.2.3 "
                        "without knowing the number of mobility-accessible units."
                    ),
                    affected_fields=["residential_accessible_parking"],
                    suggested_review_role="architect",
                )
            )

        issues.append(
            ReviewIssue(
                id="CALC-ACC-RES-002",
                category="accessible_parking",
                severity="low",
                title="Per-facility scoping not implemented",
                description=(
                    "Accessible parking should be calculated per parking facility. "
                    "This V1 calculates for the project as a whole."
                ),
                affected_fields=["residential_accessible_parking"],
                suggested_review_role="architect",
            )
        )

        results.append(self._make_result(
            "residential_accessible_parking",
            res_accessible,
            unit="spaces",
            authority_id="AUTH-ACC-RES",
            code_section="CBC 11B-208.2.3",
            formula="mobility_units + ceil(additional * 0.02)",
            inputs_used={"mobility_accessible_units": mobility_units},
            intermediate_steps=res_steps,
            confidence="medium" if mobility_units else "low",
        ))

        # --- 6b. Commercial/common accessible ---
        com_tiers = data.get("commercial_table_11B_208_2", {}).get("tiers", [])
        # Estimate commercial spaces from occupancy areas
        com_spaces = max(0, total_spaces - (project.total_units if project.total_units else 0))
        com_accessible = _lookup_commercial_accessible(com_spaces, com_tiers) if com_spaces > 0 else 0

        results.append(self._make_result(
            "commercial_accessible_parking",
            com_accessible,
            unit="spaces",
            authority_id="AUTH-ACC-COM",
            code_section="CBC 11B-208.2, Table 11B-208.2",
            formula=f"Table 11B-208.2 lookup for {com_spaces} spaces",
            inputs_used={"commercial_spaces": com_spaces},
            intermediate_steps=[f"Commercial/common spaces: {com_spaces}", f"Accessible required: {com_accessible}"],
        ))

        # --- 6c. Van accessible ---
        total_accessible = res_accessible + com_accessible
        van_data = data.get("van_accessible", {})
        divisor = van_data.get("divisor", 6)
        van_min = van_data.get("minimum", 1)
        van_accessible = max(van_min, math.ceil(total_accessible / divisor)) if total_accessible > 0 else 0

        results.append(self._make_result(
            "van_accessible_parking",
            van_accessible,
            unit="spaces",
            authority_id="AUTH-ACC-VAN",
            code_section="CBC 11B-208.2.4, 11B-502.2",
            formula=f"max({van_min}, ceil({total_accessible} / {divisor}))",
            inputs_used={"total_accessible": total_accessible},
            intermediate_steps=[
                f"Total accessible: {total_accessible}",
                f"Van: 1 per {divisor} accessible = {van_accessible}",
                "Van space: 17' wide min, 98\" vertical clearance",
            ],
        ))

        # --- 6d. Accessible EV charging ---
        evcs_data = data.get("evcs_accessible_11B_228_3", {})
        evcs_tiers = evcs_data.get("table_11B_228_3_2_1", {}).get("tiers", [])
        # Cannot determine EVCS count without knowing charging configuration
        issues.append(
            ReviewIssue(
                id="CALC-ACC-EVCS-001",
                category="accessible_parking",
                severity="medium",
                title="EVCS accessible scoping requires charging configuration input",
                description=(
                    "Accessible EVCS requirements depend on the number and type of charging stations "
                    "installed, which is not provided. Each combination of charging level + connector type "
                    "is a separate facility for scoping."
                ),
                affected_fields=["accessible_evcs"],
                suggested_review_role="architect",
            )
        )

        results.append(self._make_result(
            "accessible_evcs",
            None,
            unit="spaces",
            authority_id="AUTH-ACC-EVCS",
            code_section="CBC 11B-228.3",
            confidence="low",
            review_notes=[
                "Cannot calculate without EVCS configuration input.",
                "First EVCS must be van accessible regardless of total count.",
                "Van accessible EVCS: 144\" wide min + access aisle (11B-812.6.1).",
            ],
        ))

        return results, issues
