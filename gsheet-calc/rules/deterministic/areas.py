"""Area chain calculation rules: gross, net, effective density, buildable."""

import json

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_zone_table() -> dict:
    path = DATA_DIR / "zone_tables.json"
    return json.loads(path.read_text())


class AreaChainRule(BaseRule):
    """Computes the four area chain values for a site."""

    authority_id = "AUTH-AREA-NET"
    code_section = "LAMC 12.03"
    topic = "Area chain calculations"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []

        # 1. Gross lot area
        gross = site.lot_area_sf or site.survey_lot_area_sf
        if gross is None:
            issues.append(
                ReviewIssue(
                    id="CALC-AREA-001",
                    category="area",
                    severity="critical",
                    title="No lot area available",
                    description="Neither ZIMAS lot area nor survey lot area is provided.",
                    affected_fields=["lot_area_sf"],
                    suggested_review_role="architect",
                    blocking=True,
                )
            )
            return results, issues

        # Flag mismatch between assessor and survey
        if site.lot_area_sf and site.survey_lot_area_sf:
            diff = abs(site.lot_area_sf - site.survey_lot_area_sf)
            if diff > 50:
                issues.append(
                    ReviewIssue(
                        id="CALC-AREA-002",
                        category="area",
                        severity="high",
                        title="Lot area mismatch: assessor vs survey",
                        description=(
                            f"Assessor: {site.lot_area_sf:.0f} sf, "
                            f"Survey: {site.survey_lot_area_sf:.0f} sf "
                            f"(diff: {diff:.0f} sf). Using survey value."
                        ),
                        affected_fields=["lot_area_sf", "survey_lot_area_sf"],
                        suggested_review_role="architect",
                    )
                )
                gross = site.survey_lot_area_sf

        results.append(self._make_result(
            "gross_lot_area",
            gross,
            unit="sf",
            formula="lot_area_sf (or survey_lot_area_sf if provided)",
            inputs_used={"lot_area_sf": site.lot_area_sf, "survey_lot_area_sf": site.survey_lot_area_sf},
        ))

        # 2. Net lot area
        street_ded = project.dedication_street_ft * (site.lot_area_sf or gross) ** 0.5 if project.dedication_street_ft else 0
        # Simplified: assume dedication is frontage_length * depth
        # In practice, dedications are area-based; using frontage * depth approximation
        alley_ded = project.dedication_alley_ft * project.alley_frontage_length_ft if project.dedication_alley_ft and project.alley_frontage_length_ft else 0
        corner_cuts = project.corner_cuts_sf

        net = gross - street_ded - alley_ded - corner_cuts

        steps = [
            f"Gross: {gross:.0f} sf",
            f"Street dedication: -{street_ded:.0f} sf",
            f"Alley dedication: -{alley_ded:.0f} sf",
            f"Corner cuts: -{corner_cuts:.0f} sf",
            f"Net: {net:.0f} sf",
        ]

        results.append(self._make_result(
            "net_lot_area",
            net,
            unit="sf",
            formula="gross - street_dedication - alley_dedication - corner_cuts",
            inputs_used={
                "gross_lot_area": gross,
                "dedication_street_ft": project.dedication_street_ft,
                "dedication_alley_ft": project.dedication_alley_ft,
                "alley_frontage_length_ft": project.alley_frontage_length_ft,
                "corner_cuts_sf": project.corner_cuts_sf,
            },
            intermediate_steps=steps,
        ))

        # 3. Effective density area (net + half-alley credit)
        alley_credit = 0.0
        density_assumptions = []
        if project.alley_adjacent and project.alley_width_ft and project.alley_frontage_length_ft:
            half_alley = project.alley_width_ft / 2.0
            alley_credit = half_alley * project.alley_frontage_length_ft
            density_assumptions.append(
                f"Half-alley credit: {half_alley:.1f} ft x {project.alley_frontage_length_ft:.0f} ft = {alley_credit:.0f} sf"
            )

        effective_density_area = net + alley_credit

        confidence = "high"
        if site.zone_code_chapter == "unknown":
            confidence = "low"
            issues.append(
                ReviewIssue(
                    id="CALC-AREA-003",
                    category="area",
                    severity="high",
                    title="Chapter applicability unknown for density area calc",
                    description=(
                        "Half-alley credit rules may differ between Chapter 1 and Chapter 1A. "
                        "Chapter applicability is not established."
                    ),
                    affected_fields=["effective_density_area"],
                    suggested_review_role="zoning consultant",
                )
            )

        results.append(self._make_result(
            "effective_density_area",
            effective_density_area,
            unit="sf",
            authority_id="AUTH-AREA-DENSITY",
            formula="net_lot_area + half_alley_credit",
            inputs_used={"net_lot_area": net, "alley_credit": alley_credit},
            intermediate_steps=density_assumptions,
            confidence=confidence,
            assumptions=["Half-alley credit applied (Chapter 1 assumed)"] if alley_credit > 0 else [],
        ))

        # 4. Buildable area
        zone_data = _load_zone_table()
        zone_info = zone_data.get("zones", {}).get(site.zone or "", {})
        residential_buildable_equals_lot = zone_info.get("residential_buildable_equals_lot", False)

        if residential_buildable_equals_lot:
            buildable = net
            buildable_notes = [f"C2/C4/C5 residential exception: buildable area = net lot area ({net:.0f} sf)"]
        else:
            setbacks = zone_info.get("setbacks", {})
            front = project.setback_front_ft or setbacks.get("front_ft", 0)
            side = project.setback_side_ft or setbacks.get("side_ft", 0)
            rear = project.setback_rear_ft or setbacks.get("rear_ft", 0)
            # Simplified setback deduction (assumes rectangular lot)
            setback_area = 0.0  # Would need lot dimensions for precise calc
            buildable = net - setback_area
            buildable_notes = [
                f"Setbacks: front={front}, side={side}, rear={rear}",
                "Note: Precise setback area deduction requires lot dimensions. Using net lot area as buildable.",
            ]
            if not project.setback_front_ft:
                buildable_notes.append(f"Using zone default setbacks for {site.zone}")

        results.append(self._make_result(
            "buildable_area",
            buildable,
            unit="sf",
            authority_id="AUTH-AREA-BUILDABLE",
            code_section="LAMC 12.21.1",
            formula="net_lot_area - setback_area (or net for C2/C4/C5 residential)",
            inputs_used={"net_lot_area": net, "zone": site.zone},
            intermediate_steps=buildable_notes,
            review_notes=["Setback area deduction is simplified; verify with actual lot geometry."],
        ))

        return results, issues
