"""TOC density increase calculation (Step 5a).

Computes density bonus under LAMC 12.22 A.31 based on TOC tier.
"""

from __future__ import annotations

import math

from density.models import BaselineDensity, DensityIssue, ParcelRegime, TOCDensity

# TOC tier density increase percentages (LAMC 12.22 A.31)
TOC_TIER_PERCENTAGES: dict[int, float] = {
    1: 0.50,
    2: 0.60,
    3: 0.70,
    4: 0.80,
}


def _lamc_round(raw: float) -> int:
    """LAMC rounding: floor, except exactly 0.5 rounds up."""
    fraction = raw - math.floor(raw)
    if abs(fraction - 0.5) < 1e-9:
        return math.ceil(raw)
    return math.floor(raw)


def compute_toc_density(
    regime: ParcelRegime,
    baseline: BaselineDensity,
) -> TOCDensity:
    """STEP 5a: Compute TOC density increase."""
    issues: list[DensityIssue] = []

    tier = regime.toc_tier_zimas
    tier_verified = regime.toc_tier_verified

    if tier is None:
        issues.append(DensityIssue(
            step="STEP_5a_toc_density",
            field="toc_tier",
            severity="error",
            message="No TOC tier available. Cannot compute TOC density increase.",
            action_required="Confirm TOC tier from HCIDLA or Metro transit stop data.",
            confidence_impact="degrades_to_unresolved",
        ))
        return TOCDensity(status="provisional", issues=issues)

    if tier not in TOC_TIER_PERCENTAGES:
        issues.append(DensityIssue(
            step="STEP_5a_toc_density",
            field="toc_tier",
            severity="error",
            message=f"TOC tier {tier} not recognized. Must be 1-4.",
            action_required="Verify TOC tier designation.",
            confidence_impact="degrades_to_unresolved",
        ))
        return TOCDensity(tier=tier, status="provisional", issues=issues)

    if baseline.baseline_units is None:
        issues.append(DensityIssue(
            step="STEP_5a_toc_density",
            field="baseline_units",
            severity="error",
            message="Baseline units not available. Cannot compute TOC bonus.",
            confidence_impact="degrades_to_unresolved",
        ))
        return TOCDensity(tier=tier, status="provisional", issues=issues)

    pct = TOC_TIER_PERCENTAGES[tier]
    bonus_raw = baseline.baseline_units * pct
    bonus_units = _lamc_round(bonus_raw)
    total_units = baseline.baseline_units + bonus_units

    tier_source = "ZIMAS" if not tier_verified else "verified"
    if not tier_verified:
        issues.append(DensityIssue(
            step="STEP_5a_toc_density",
            field="toc_tier",
            severity="warning",
            message=f"TOC tier {tier} from ZIMAS only, not independently verified.",
            action_required="Verify TOC tier with HCIDLA or Metro transit stop data.",
            confidence_impact="degrades_to_provisional",
        ))

    status = "provisional" if not tier_verified else "confirmed"

    return TOCDensity(
        tier=tier,
        tier_source=tier_source,
        tier_verified=tier_verified,
        percentage_increase=pct,
        bonus_units=bonus_units,
        total_units=total_units,
        status=status,
        issues=issues,
    )
