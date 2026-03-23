"""Base density calculation rule."""

import json
import math

from config.settings import DATA_DIR
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.base import BaseRule


def _load_zone_table() -> dict:
    path = DATA_DIR / "zone_tables.json"
    return json.loads(path.read_text())


class DensityRule(BaseRule):
    """Calculate base zoning density (units allowed)."""

    authority_id = "AUTH-DENSITY"
    code_section = "LAMC 12.04 et seq."
    topic = "Base density"

    def evaluate(self, site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
        results: list[CalcResult] = []
        issues: list[ReviewIssue] = []

        zone_data = _load_zone_table()
        zone_info = zone_data.get("zones", {}).get(site.zone or "", {})

        if not zone_info:
            issues.append(
                ReviewIssue(
                    id="CALC-DENS-001",
                    category="density",
                    severity="critical",
                    title=f"Zone '{site.zone}' not found in zone tables",
                    description="Cannot determine density factor. Manual lookup required.",
                    affected_fields=["base_density"],
                    suggested_review_role="zoning consultant",
                    blocking=True,
                )
            )
            return results, issues

        density_factor = zone_info.get("density_factor_sf")
        if density_factor is None:
            issues.append(
                ReviewIssue(
                    id="CALC-DENS-002",
                    category="density",
                    severity="high",
                    title=f"No density factor for zone '{site.zone}'",
                    description="Zone exists in table but density_factor_sf is null. Likely an authority gap.",
                    affected_fields=["base_density"],
                    suggested_review_role="zoning consultant",
                    blocking=True,
                )
            )
            return results, issues

        # Need effective density area from prior calc
        # For now, compute from site data directly
        effective_area = site.lot_area_sf or 0
        if effective_area <= 0:
            issues.append(
                ReviewIssue(
                    id="CALC-DENS-003",
                    category="density",
                    severity="critical",
                    title="No lot area for density calculation",
                    affected_fields=["base_density"],
                    description="Lot area is zero or missing.",
                    blocking=True,
                )
            )
            return results, issues

        base_density = math.floor(effective_area / density_factor)

        results.append(self._make_result(
            "base_density",
            base_density,
            unit="dwelling units",
            formula=f"floor({effective_area:.0f} / {density_factor})",
            inputs_used={
                "effective_density_area": effective_area,
                "density_factor_sf": density_factor,
                "zone": site.zone,
            },
            intermediate_steps=[
                f"Effective density area: {effective_area:.0f} sf",
                f"Zone {site.zone} density factor: 1 unit per {density_factor} sf",
                f"Raw: {effective_area / density_factor:.2f} -> floor = {base_density}",
            ],
            review_notes=["Bonus density (TOC, State DB) NOT included. See advisory screens."],
        ))

        return results, issues
