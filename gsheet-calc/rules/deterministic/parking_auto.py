"""Residential and commercial auto parking requirement rules."""

import json
import math

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_parking_ratios() -> dict:
    path = DATA_DIR / "parking_ratios.json"
    return json.loads(path.read_text())


class AutoParkingRule(BaseRule):
    """Calculate required auto parking spaces for residential and commercial uses."""

    authority_id = "AUTH-PARK-RES"
    code_section = "LAMC 12.21 A.4"
    topic = "Auto parking requirements"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []
        ratios = _load_parking_ratios()

        # --- Residential parking ---
        res_required = self._calc_residential(site, project, ratios, results, issues)

        # --- Commercial parking ---
        com_required = self._calc_commercial(project, ratios, results, issues)

        # --- Total ---
        total = res_required + com_required
        results.append(self._make_result(
            "total_parking_required",
            total,
            unit="spaces",
            formula=f"{res_required} + {com_required}",
            inputs_used={"residential": res_required, "commercial": com_required},
        ))

        return results, issues

    def _calc_residential(
        self, site: Site, project: Project, ratios: dict,
        results: list[CalcResult], issues: list[ReviewIssue],
    ) -> int:
        """Calculate residential parking, returning required spaces count."""
        res_spaces = 0.0
        steps = []

        # Check zone-specific overrides (e.g. R2: flat 2 spaces/unit)
        zone_overrides = ratios.get("residential_zone_overrides", {})
        zone_override = zone_overrides.get(site.zone or "") if site else None

        if zone_override:
            flat_rate = zone_override["spaces_per_unit"]
            total_units = sum(ut.count for ut in project.unit_mix) if project.unit_mix else project.total_units
            res_spaces = float(total_units) * flat_rate
            steps.append(
                f"Zone {site.zone} override: {total_units} units x {flat_rate} spaces/unit = {res_spaces:.1f}"
            )
            res_required = math.ceil(res_spaces)
            steps.append(f"Total residential: {res_spaces:.1f} -> ceil = {res_required}")

            results.append(self._make_result(
                "residential_parking_required",
                res_required,
                unit="spaces",
                formula=f"zone_override: {total_units} * {flat_rate}",
                inputs_used={"zone": site.zone, "total_units": total_units, "flat_rate": flat_rate},
                intermediate_steps=steps,
                review_notes=[
                    f"Zone {site.zone}: flat rate {flat_rate} spaces/unit (not hab-room-based). {zone_override.get('notes', '')}",
                    "Incentive parking reductions NOT applied. See advisory screens.",
                ],
            ))
            return res_required

        # Default: hab-room-based tiers
        tiers = ratios.get("residential", {}).get("tiers", [])

        if not project.unit_mix:
            if project.total_units > 0:
                issues.append(
                    ReviewIssue(
                        id="CALC-PARK-001",
                        category="parking",
                        severity="medium",
                        title="No unit mix provided",
                        description="Using total_units with default assumption of <3 habitable rooms (1 space/unit).",
                        affected_fields=["residential_parking"],
                        suggested_review_role="architect",
                    )
                )
                res_spaces = float(project.total_units)
                steps.append(f"Default: {project.total_units} units x 1.0 space = {res_spaces:.1f}")
        else:
            for ut in project.unit_mix:
                rate = 1.0
                for tier in tiers:
                    if "habitable_rooms_max" in tier and ut.habitable_rooms <= tier["habitable_rooms_max"]:
                        rate = tier["spaces_per_unit"]
                        break
                    if "habitable_rooms_exact" in tier and ut.habitable_rooms == tier["habitable_rooms_exact"]:
                        rate = tier["spaces_per_unit"]
                        break
                    if "habitable_rooms_min" in tier and ut.habitable_rooms >= tier["habitable_rooms_min"]:
                        rate = tier["spaces_per_unit"]
                        break
                unit_spaces = ut.count * rate
                res_spaces += unit_spaces
                steps.append(
                    f"{ut.label}: {ut.count} units x {rate} spaces "
                    f"({ut.habitable_rooms} hab rooms) = {unit_spaces:.1f}"
                )

        res_required = math.ceil(res_spaces)
        steps.append(f"Total residential: {res_spaces:.1f} -> ceil = {res_required}")

        results.append(self._make_result(
            "residential_parking_required",
            res_required,
            unit="spaces",
            formula="sum(units * rate_per_habitable_rooms)",
            inputs_used={"unit_mix": [u.model_dump() for u in project.unit_mix] if project.unit_mix else {"total_units": project.total_units}},
            intermediate_steps=steps,
            review_notes=["Incentive parking reductions NOT applied. See advisory screens."],
        ))
        return res_required

    def _calc_commercial(
        self, project: Project, ratios: dict,
        results: list[CalcResult], issues: list[ReviewIssue],
    ) -> int:
        """Calculate commercial parking, returning required spaces count."""
        com_spaces = 0.0
        com_steps = []
        com_uses = ratios.get("commercial", {}).get("uses", {})

        for occ in project.occupancy_areas:
            if occ.occupancy_group in ("R-2", "R-1"):
                continue  # Skip residential occupancy
            use_key = _map_occupancy_to_use(occ)
            use_info = com_uses.get(use_key)
            if use_info:
                per_unit_sf = float(use_info["per_unit"].replace(" sf", "").replace(",", ""))
                spaces = (occ.area_sf / per_unit_sf) * use_info["ratio"]
                com_spaces += spaces
                com_steps.append(
                    f"{occ.use_description}: {occ.area_sf:.0f} sf / {per_unit_sf:.0f} sf x {use_info['ratio']} = {spaces:.1f}"
                )
            else:
                issues.append(
                    ReviewIssue(
                        id=f"CALC-PARK-COM-{occ.occupancy_group}",
                        category="parking",
                        severity="medium",
                        title=f"No parking ratio for occupancy '{occ.occupancy_group}'",
                        description=f"Use '{occ.use_description}' not mapped to a parking ratio.",
                        affected_fields=["commercial_parking"],
                        suggested_review_role="architect",
                    )
                )

        com_required = math.ceil(com_spaces)
        if com_steps:
            com_steps.append(f"Total commercial: {com_spaces:.1f} -> ceil = {com_required}")

        results.append(self._make_result(
            "commercial_parking_required",
            com_required,
            unit="spaces",
            authority_id="AUTH-PARK-COM",
            formula="sum(area_sf / per_unit_sf * ratio)",
            inputs_used={"occupancy_areas": [o.model_dump() for o in project.occupancy_areas]},
            intermediate_steps=com_steps,
        ))
        return com_required


def _map_occupancy_to_use(occ) -> str:
    """Map an OccupancyArea to a parking ratio use key."""
    desc = occ.use_description.lower()
    if "retail" in desc or occ.occupancy_group == "M":
        return "retail"
    if "office" in desc or occ.occupancy_group == "B":
        return "office"
    if "restaurant" in desc or "dining" in desc:
        return "restaurant"
    if "medical" in desc:
        return "medical_office"
    return occ.use_description.lower().replace(" ", "_")
