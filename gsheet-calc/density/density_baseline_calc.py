"""Baseline density calculation (Step 3).

Computes max unit count from lot area and governing density factor,
before any incentive bonuses.
"""

from __future__ import annotations

import math

from density.models import (
    AuthorityInterrupters,
    BaselineDensity,
    DensityIssue,
    ParcelRegime,
)
from models.site import Site

# Material difference threshold for survey vs fallback lot area.
# 2% is used because:
#   - ZIMAS parcel areas derive from assessor/GIS data with known rounding
#   - survey areas are field-measured and authoritative
#   - a >2% gap often means dedications, lot-line adjustments, or data error
#   - below 2%, the difference rarely changes the unit count for typical density factors
_LOT_AREA_MATERIAL_DIFF_PCT = 2.0


def _lamc_round(raw: float) -> int:
    """LAMC 12.22 A.18 rounding: floor, except exactly 0.5 rounds up."""
    fraction = raw - math.floor(raw)
    if abs(fraction - 0.5) < 1e-9:
        return math.ceil(raw)
    return math.floor(raw)


def _resolve_lot_area(site: Site) -> tuple[float | None, str, str, list[DensityIssue]]:
    """Select lot area and assess basis confidence.

    Returns:
        (lot_area, lot_area_source, lot_area_basis_confidence, issues)
    """
    issues: list[DensityIssue] = []
    has_survey = site.survey_lot_area_sf is not None and site.survey_lot_area_sf > 0
    has_fallback = site.lot_area_sf is not None and site.lot_area_sf > 0

    if not has_survey and not has_fallback:
        issues.append(DensityIssue(
            step="STEP_3_baseline_density",
            field="lot_area",
            severity="error",
            message="No lot area available for density calculation.",
            action_required="Confirm lot area from survey or ZIMAS.",
            confidence_impact="degrades_to_unresolved",
        ))
        return None, "", "unresolved", issues

    # Case: survey available
    if has_survey:
        lot_area = site.survey_lot_area_sf
        source = "survey"
        basis_confidence = "confirmed"

        # Check for material difference with fallback if both exist
        if has_fallback:
            diff_pct = abs(lot_area - site.lot_area_sf) / site.lot_area_sf * 100.0
            if diff_pct > _LOT_AREA_MATERIAL_DIFF_PCT:
                issues.append(DensityIssue(
                    step="STEP_3_baseline_density",
                    field="lot_area",
                    severity="warning",
                    message=(
                        f"Survey lot area ({lot_area:,.0f} sf) differs from "
                        f"ZIMAS/assessor lot area ({site.lot_area_sf:,.0f} sf) "
                        f"by {diff_pct:.1f}% (threshold: {_LOT_AREA_MATERIAL_DIFF_PCT}%). "
                        f"Using survey. Difference may indicate dedications, lot-line adjustments, or data error."
                    ),
                    action_required="Confirm which lot area is the correct density denominator basis.",
                    confidence_impact="degrades_to_provisional",
                ))
                basis_confidence = "provisional"

        return lot_area, source, basis_confidence, issues

    # Case: only ZIMAS/assessor lot area, no survey
    issues.append(DensityIssue(
        step="STEP_3_baseline_density",
        field="lot_area",
        severity="info",
        message=(
            f"Using ZIMAS/assessor lot area ({site.lot_area_sf:,.0f} sf). "
            f"No survey lot area provided. ZIMAS areas are GIS-derived and may "
            f"not reflect actual field conditions, dedications, or lot-line adjustments."
        ),
        action_required="Provide survey lot area for confirmed density denominator.",
        confidence_impact="degrades_to_provisional",
    ))
    return site.lot_area_sf, "zimas", "provisional", issues


def compute_baseline_density(
    site: Site,
    regime: ParcelRegime,
    interrupters: AuthorityInterrupters,
) -> BaselineDensity:
    """STEP 3: Compute baseline unit count without incentives.

    Status reflects the WORST of:
      - upstream authority confidence
      - density factor source (governing vs baseline fallback)
      - lot area basis confidence
    """
    issues: list[DensityIssue] = []

    # ── Resolve density factor ──────────────────────────────────────
    # Prefer governing (authority-cleared) density; fall back to baseline (zone-derived)
    # when governing is unavailable due to unresolved interrupters. The baseline value
    # still lets us compute a provisional unit count even though it is not yet governing.
    sf_per_du = interrupters.governing_density_sf_per_du
    sf_per_du_source = interrupters.governing_density_source
    used_governing = sf_per_du is not None

    if not used_governing:
        sf_per_du = interrupters.baseline_density_sf_per_du
        sf_per_du_source = interrupters.baseline_density_source
        if sf_per_du is not None:
            issues.append(DensityIssue(
                step="STEP_3_baseline_density",
                field="sf_per_du",
                severity="warning",
                message=(
                    f"Governing density factor unavailable (authority interrupters unresolved). "
                    f"Falling back to zone-derived baseline density: {sf_per_du} sf/du "
                    f"from {sf_per_du_source}. Result is provisional until authority chain is cleared."
                ),
                action_required="Resolve authority interrupters to confirm governing density.",
                confidence_impact="degrades_to_provisional",
            ))

    if sf_per_du is None or sf_per_du <= 0:
        issues.append(DensityIssue(
            step="STEP_3_baseline_density",
            field="sf_per_du",
            severity="error",
            message="No valid density factor (sf/du) available. Cannot compute baseline.",
            action_required="Resolve density standard from authority chain.",
            confidence_impact="degrades_to_unresolved",
        ))
        return BaselineDensity(status="unresolved", issues=issues)

    # ── Resolve lot area basis ──────────────────────────────────────
    lot_area, lot_area_source, lot_area_basis_confidence, lot_issues = _resolve_lot_area(site)
    issues.extend(lot_issues)

    if lot_area is None or lot_area <= 0:
        return BaselineDensity(status="unresolved", issues=issues)

    # ── Arithmetic ──────────────────────────────────────────────────
    raw = lot_area / sf_per_du
    baseline_units = _lamc_round(raw)

    # ── Status: worst-of upstream authority, density-factor source, lot-area basis ──
    # Each axis contributes a ceiling on status.
    status = "confirmed"

    # Upstream authority confidence
    if interrupters.confidence == "unresolved":
        status = "unresolved"
    elif interrupters.confidence == "provisional":
        status = "provisional"

    # Density factor source: fallback to baseline degrades to at least provisional
    if not used_governing and status == "confirmed":
        status = "provisional"

    # Lot area basis confidence
    if lot_area_basis_confidence == "unresolved":
        status = "unresolved"
    elif lot_area_basis_confidence == "provisional" and status == "confirmed":
        status = "provisional"

    return BaselineDensity(
        lot_area_used=lot_area,
        lot_area_source=lot_area_source,
        lot_area_basis_confidence=lot_area_basis_confidence,
        sf_per_du_used=sf_per_du,
        sf_per_du_source=sf_per_du_source,
        used_governing_density=used_governing,
        raw_calculation=raw,
        baseline_units=baseline_units,
        rounding_rule_applied="LAMC_12.22_A.18_floor_except_0.5_up",
        status=status,
        issues=issues,
    )
