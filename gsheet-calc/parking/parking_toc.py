"""TOC parking reduction (Parking Step 5a).

Computes reduced parking under LAMC 12.22 A.31 based on TOC tier.

Implementation scope disclosure:
  This module currently implements:
    - Standard TOC parking rate (0.5 spaces/unit) for all tiers 1-4
    - 100% affordable zero-parking path (0 spaces/unit)
  This module does NOT yet implement:
    - Tier-differentiated parking rates if the operative TOC guidelines
      prescribe rates other than 0.5/unit for specific tiers
    - TOC parking rates for non-residential components of mixed-use projects
    - Special TOC parking provisions for projects with commercial components
    - Filing-date-dependent TOC guideline amendments
  The current 0.5/unit rate across all tiers reflects a common reading of
  the TOC guidelines, but should be verified against the operative guidelines
  at time of filing. If tier-specific rates differ, this module will need
  updating.

Rounding convention:
  math.ceil(total_units * rate_per_unit) — local implementation convention.
"""

from __future__ import annotations

import math

from parking.models import ParkingIssue, TOCParking

# TOC parking rates per unit (LAMC 12.22 A.31).
# Currently all tiers use 0.5/unit. This reflects a common reading but
# should be verified against operative TOC guidelines at filing.
# If tier-specific rates are confirmed to differ, update this table.
TOC_PARKING_RATES: dict[int, float] = {
    1: 0.5,
    2: 0.5,
    3: 0.5,
    4: 0.5,
}

# 100% affordable under TOC may qualify for 0 spaces/unit.
# This is a provisional rate pending explicit confirmation.
TOC_100_AFFORDABLE_RATE = 0.0

_BRANCHES_NOT_IMPLEMENTED = [
    "tier-differentiated rates (if operative guidelines prescribe non-0.5 for specific tiers)",
    "non-residential mixed-use component parking under TOC",
    "filing-date-dependent TOC guideline amendments",
]


def compute_toc_parking(
    tier: int | None,
    total_units: int,
    tier_verified: bool = False,
    is_100_pct_affordable: bool = False,
    is_100_pct_affordable_confirmed: bool = False,
) -> TOCParking:
    """STEP 5a: Compute TOC parking reduction.

    Args:
        tier: TOC tier (1-4) or None if unknown.
        total_units: Total unit count from density module.
        tier_verified: Whether tier has been independently verified
            (not just ZIMAS-reported).
        is_100_pct_affordable: Whether project plans/intends 100% affordable.
        is_100_pct_affordable_confirmed: Whether 100% affordable treatment
            has been explicitly confirmed for TOC parking purposes.
    """
    issues: list[ParkingIssue] = []

    if tier is None or tier not in TOC_PARKING_RATES:
        issues.append(ParkingIssue(
            step="STEP_5a_toc_parking",
            field="tier",
            severity="warning",
            message=f"TOC tier {'unknown' if tier is None else tier} not recognized or not available. Cannot compute TOC parking.",
            action_required="Confirm TOC tier (1-4) from HCIDLA or Metro transit data.",
            confidence_impact="degrades_to_unresolved",
        ))
        return TOCParking(
            tier=tier,
            tier_verified=tier_verified,
            status="provisional",
            issues=issues,
        )

    # ── Determine rate and branch ───────────────────────────────────
    if is_100_pct_affordable:
        rate = TOC_100_AFFORDABLE_RATE
        branch = "100pct_affordable_zero"

        if not is_100_pct_affordable_confirmed:
            issues.append(ParkingIssue(
                step="STEP_5a_toc_parking",
                field="is_100_pct_affordable",
                severity="warning",
                message=(
                    "100% affordable TOC parking treatment (0 spaces/unit) applied as "
                    "provisional working assumption based on project affordability plan. "
                    "Treatment is not yet explicitly confirmed. Final eligibility for "
                    "zero-parking under TOC should be verified against operative TOC guidelines."
                ),
                action_required="Confirm 100% affordable treatment eligibility for TOC parking.",
                confidence_impact="degrades_to_provisional",
            ))
    else:
        rate = TOC_PARKING_RATES[tier]
        branch = "standard_0.5_per_unit"

        # Disclose that all tiers currently use the same rate
        issues.append(ParkingIssue(
            step="STEP_5a_toc_parking",
            field="rate_per_unit",
            severity="info",
            message=(
                f"TOC Tier {tier} parking rate: {rate} spaces/unit. "
                f"This module currently applies 0.5/unit for all tiers (1-4). "
                f"Verify this rate against the operative TOC guidelines at filing, "
                f"as tier-specific rates may differ."
            ),
            confidence_impact="none",
        ))

    required = math.ceil(total_units * rate)

    # ── Tier verification status ────────────────────────────────────
    if not tier_verified:
        issues.append(ParkingIssue(
            step="STEP_5a_toc_parking",
            field="tier",
            severity="warning",
            message=(
                f"TOC Tier {tier} is from ZIMAS only, not independently verified. "
                f"ZIMAS tier data is a reference signal, not final confirmation."
            ),
            action_required="Verify TOC tier with HCIDLA or Metro transit stop data.",
            confidence_impact="degrades_to_provisional",
        ))

    # ── Implementation scope disclosure ─────────────────────────────
    issues.append(ParkingIssue(
        step="STEP_5a_toc_parking",
        field="implementation_scope",
        severity="info",
        message=(
            f"TOC parking computed using '{branch}' branch. "
            f"This module does not yet implement: "
            f"{'; '.join(_BRANCHES_NOT_IMPLEMENTED)}."
        ),
        confidence_impact="none",
    ))

    # ── Status determination ────────────────────────────────────────
    # Status can reach confirmed ONLY when ALL of:
    #   - tier is verified
    #   - if 100% affordable, that treatment is confirmed
    #   - the implemented branch is the applicable one
    # Since this module uses a simplified branch (0.5/unit for all tiers),
    # and we cannot verify that the operative guidelines match without
    # external confirmation, the ceiling is provisional.
    status = "provisional"

    return TOCParking(
        tier=tier,
        tier_verified=tier_verified,
        rate_per_unit=rate,
        total_units=total_units,
        required_spaces=required,
        is_100_pct_affordable=is_100_pct_affordable,
        is_100_pct_affordable_confirmed=is_100_pct_affordable_confirmed,
        total_rounding_convention="ceil_units_times_rate",
        implemented_branch=branch,
        branches_not_implemented=list(_BRANCHES_NOT_IMPLEMENTED),
        status=status,
        issues=issues,
    )
