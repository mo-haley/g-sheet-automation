"""State Density Bonus density increase calculation (Step 5b).

Implements Government Code 65915 density bonus, including:
- Three-way base density comparison (zoning, specific plan, GP)
- Sliding scale by income category
- 100% affordable unlimited density (AB 1287)

Base density comparison note:
  Gov. Code 65915 requires the bonus to be computed on the MAXIMUM of
  zoning density, specific plan density, and General Plan density.
  This file evaluates whichever legs are available and explicitly tracks
  which legs were evaluated vs unresolved. A "governing base" is only
  marked confirmed when all applicable legs have been checked.
"""

from __future__ import annotations

import math

from density.models import (
    AuthorityInterrupters,
    BaselineDensity,
    DensityIssue,
    GPDensityLookup,
    ParcelRegime,
    StateDBAffordableSetAside,
    StateDBDensity,
)
from models.project import AffordabilityPlan, Project
from models.site import Site

# Standard sliding scale: (income_category, min_pct_threshold, base_bonus_pct, incremental_per_1pct)
BONUS_SCHEDULE_RENTAL = {
    "vli": {"min_pct": 5, "base_bonus": 20, "per_additional_pct": 2.5, "cap": 50},
    "li":  {"min_pct": 10, "base_bonus": 20, "per_additional_pct": 1.5, "cap": 50},
    "moderate": {"min_pct": 10, "base_bonus": 5, "per_additional_pct": 1.0, "cap": 50},
}

BONUS_SCHEDULE_FOR_SALE = {
    "li":  {"min_pct": 10, "base_bonus": 20, "per_additional_pct": 1.5, "cap": 50},
    "moderate": {"min_pct": 10, "base_bonus": 5, "per_additional_pct": 1.0, "cap": 50},
}

# Rounding rule for DB bonus unit calculation.
# Gov. Code 65915 does not specify its own rounding rule. LAMC 12.22 A.18
# (floor, except exactly 0.5 rounds up) is used as the local implementation
# default. This is explicitly documented so it can be challenged or overridden
# if a different rule is determined to apply.
_DB_ROUNDING_RULE = "LAMC_12.22_A.18_floor_except_0.5_up_applied_to_DB_by_local_convention"


def _db_round(raw: float) -> int:
    """Round DB bonus units per local convention.

    Gov. Code 65915 does not specify rounding. We apply LAMC 12.22 A.18
    (floor, except exactly 0.5 rounds up) as the local default.
    This is isolated here so it can be replaced if a DB-specific rule is confirmed.
    """
    fraction = raw - math.floor(raw)
    if abs(fraction - 0.5) < 1e-9:
        return math.ceil(raw)
    return math.floor(raw)


def _compute_bonus_pct(affordability: AffordabilityPlan, is_for_sale: bool) -> float:
    """Compute the highest applicable density bonus percentage from the sliding scale."""
    schedule = BONUS_SCHEDULE_FOR_SALE if is_for_sale else BONUS_SCHEDULE_RENTAL
    max_bonus = 0.0

    pct_map = {
        "vli": affordability.vli_pct,
        "li": affordability.li_pct,
        "moderate": affordability.moderate_pct,
    }

    for category, params in schedule.items():
        set_aside = pct_map.get(category, 0.0)
        if set_aside >= params["min_pct"]:
            additional = set_aside - params["min_pct"]
            bonus = params["base_bonus"] + (additional * params["per_additional_pct"])
            bonus = min(bonus, params["cap"])
            max_bonus = max(max_bonus, bonus)

    # ELI (extremely low income) bonus for rental
    if not is_for_sale and affordability.eli_pct > 0:
        eli_bonus = 20 + (affordability.eli_pct * 2.5)
        eli_bonus = min(eli_bonus, 50)
        max_bonus = max(max_bonus, eli_bonus)

    return max_bonus


def _evaluate_gp_leg(
    gp_lookup: GPDensityLookup | None,
    lot_area: float,
) -> tuple[int | None, list[DensityIssue], bool]:
    """Attempt to compute GP-based density from the GP density scaffold.

    Returns:
        (gp_units or None, issues, was_resolved)
    """
    issues: list[DensityIssue] = []

    if gp_lookup is None or not gp_lookup.gp_density_resolved:
        designation = gp_lookup.designation if gp_lookup else "unknown"
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="base_density_gp",
            severity="warning",
            message=(
                f"GP land use '{designation}' density range not resolved. "
                f"GP leg of three-way comparison is unresolved. "
                f"If GP density is higher than zoning, the DB base may be understated."
            ),
            action_required="Determine GP land use density and compare with zoning for DB base.",
            confidence_impact="degrades_to_provisional",
        ))
        return None, issues, False

    # GP scaffold provides a range. For DB base density comparison,
    # use the most favorable (lowest sf/du = highest unit count) end of the range,
    # since Gov. Code 65915 uses the maximum allowable density.
    gp_sf_per_du = gp_lookup.min_sf_per_du
    if gp_sf_per_du is None or gp_sf_per_du <= 0:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="base_density_gp",
            severity="info",
            message=f"GP density range for '{gp_lookup.designation}' has no usable min_sf_per_du.",
            confidence_impact="none",
        ))
        return None, issues, False

    gp_units = _db_round(lot_area / gp_sf_per_du)
    issues.append(DensityIssue(
        step="STEP_5b_state_db",
        field="base_density_gp",
        severity="info",
        message=(
            f"GP '{gp_lookup.designation}' most favorable density: "
            f"{lot_area:,.0f} sf / {gp_sf_per_du} sf/du = {gp_units} units "
            f"(source: {gp_lookup.source}). "
            f"GP scaffold is approximate; verify if GP density is contested."
        ),
        confidence_impact="none",
    ))
    return gp_units, issues, True


def compute_state_db_density(
    site: Site,
    regime: ParcelRegime,
    interrupters: AuthorityInterrupters,
    baseline: BaselineDensity,
    project: Project,
) -> StateDBDensity:
    """STEP 5b: Compute State Density Bonus increase.

    Three-way base density comparison per Gov. Code 65915:
    DB base = max(zoning density, specific plan density, GP density).
    Each leg is evaluated if data is available; unresolved legs are tracked explicitly.

    Status reflects the worst of:
      - upstream baseline status
      - completeness of three-way comparison
      - affordability confirmation
    """
    issues: list[DensityIssue] = []
    legs_evaluated: list[str] = []
    legs_unresolved: list[str] = []

    # ── Gate: baseline must be available ────────────────────────────
    if baseline.baseline_units is None:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="baseline_units",
            severity="error",
            message="Baseline units not available. Cannot compute State DB.",
            confidence_impact="degrades_to_unresolved",
        ))
        return StateDBDensity(status="unresolved", issues=issues)

    # Use the same resolved lot area as baseline density (fix #2)
    lot_area = baseline.lot_area_used
    if lot_area is None or lot_area <= 0:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="lot_area",
            severity="error",
            message="No resolved lot area from baseline density. Cannot compute DB base candidates.",
            confidence_impact="degrades_to_unresolved",
        ))
        return StateDBDensity(status="unresolved", issues=issues)

    # ── Leg 1: Zoning density (always available if baseline exists) ─
    base_zoning = baseline.baseline_units
    legs_evaluated.append("zoning")

    # ── Leg 2: Specific plan density ────────────────────────────────
    base_sp: int | None = None
    if interrupters.specific_plan_density_sf_per_du:
        base_sp = _db_round(lot_area / interrupters.specific_plan_density_sf_per_du)
        legs_evaluated.append("specific_plan")
    elif regime.specific_plan:
        # Specific plan exists but density not parsed — unresolved leg
        legs_unresolved.append("specific_plan")
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="base_density_specific_plan",
            severity="warning",
            message=(
                f"Specific plan '{regime.specific_plan}' exists but its density "
                f"has not been parsed. Specific plan leg of three-way comparison is unresolved."
            ),
            action_required=f"Parse specific plan '{regime.specific_plan}' for density provisions.",
            confidence_impact="degrades_to_provisional",
        ))
    # else: no specific plan — leg not applicable, no issue

    # ── Leg 3: General Plan density ─────────────────────────────────
    base_gp: int | None = None
    gp_lookup = interrupters.gp_density_lookup
    gp_units, gp_issues, gp_resolved = _evaluate_gp_leg(gp_lookup, lot_area)
    issues.extend(gp_issues)
    if gp_resolved:
        base_gp = gp_units
        legs_evaluated.append("general_plan")
    else:
        legs_unresolved.append("general_plan")

    # ── Governing DB base = max of evaluated legs ───────────────────
    candidates: list[tuple[str, int]] = [("zoning", base_zoning)]
    if base_sp is not None:
        candidates.append(("specific_plan", base_sp))
    if base_gp is not None:
        candidates.append(("general_plan", base_gp))

    governing_source, governing_base = max(candidates, key=lambda x: x[1])

    # Governing base is only confirmed when all applicable legs were evaluated
    governing_base_confirmed = len(legs_unresolved) == 0

    if not governing_base_confirmed:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="governing_base_density",
            severity="warning",
            message=(
                f"DB base density comparison incomplete. "
                f"Evaluated: {legs_evaluated}. Unresolved: {legs_unresolved}. "
                f"Current best: {governing_base} units from {governing_source}. "
                f"Actual DB base may be higher if unresolved legs yield more units."
            ),
            action_required="Resolve unresolved comparison legs to confirm DB base density.",
            confidence_impact="degrades_to_provisional",
        ))

    # ── Affordability ───────────────────────────────────────────────
    affordability = project.affordability
    if not affordability:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="affordable_set_aside",
            severity="error",
            message="No affordability plan provided. Cannot compute State DB bonus percentage.",
            action_required="Provide affordability set-aside percentages by income category.",
            confidence_impact="degrades_to_unresolved",
        ))
        return StateDBDensity(
            base_density_zoning=base_zoning,
            base_density_specific_plan=base_sp,
            base_density_gp=base_gp,
            comparison_legs_evaluated=legs_evaluated,
            comparison_legs_unresolved=legs_unresolved,
            governing_base_density=governing_base,
            governing_base_source=governing_source,
            governing_base_confirmed=governing_base_confirmed,
            status="unresolved",
            issues=issues,
        )

    is_for_sale = project.for_sale or False

    set_aside = StateDBAffordableSetAside(
        eli_pct=affordability.eli_pct,
        vli_pct=affordability.vli_pct,
        li_pct=affordability.li_pct,
        moderate_pct=affordability.moderate_pct,
    )

    # ── Status floor from upstream ──────────────────────────────────
    # DB result cannot be more confident than its inputs
    status_ceiling = "confirmed"
    if baseline.status == "unresolved":
        status_ceiling = "unresolved"
    elif baseline.status == "provisional":
        status_ceiling = "provisional"
    if not governing_base_confirmed and status_ceiling == "confirmed":
        status_ceiling = "provisional"

    # ── 100% affordable path (fix #4) ──────────────────────────────
    total_affordable_pct = (
        affordability.eli_pct + affordability.vli_pct +
        affordability.li_pct + affordability.moderate_pct
    )
    percentage_indicates_100_aff = total_affordable_pct >= 100.0

    if percentage_indicates_100_aff:
        # Percentage alone indicates 100% affordable, but this is not
        # a confirmed eligibility determination. Unlimited density under
        # AB 1287 / Gov. Code 65915(f) requires verification beyond
        # percentage math (e.g., unit-by-unit affordability covenant,
        # income targeting confirmation, manager unit treatment).
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="is_100_pct_affordable",
            severity="warning",
            message=(
                f"Affordability percentages sum to {total_affordable_pct:.0f}%, "
                f"indicating potential 100% affordable status. Unlimited density "
                f"under AB 1287 / Gov. Code 65915(f) requires explicit eligibility "
                f"verification beyond percentage math (covenant confirmation, "
                f"income targeting, manager unit treatment)."
            ),
            action_required="Confirm 100% affordable eligibility with HCIDLA or project counsel.",
            confidence_impact="degrades_to_provisional",
        ))

        # Status for 100% affordable path: always provisional until explicitly confirmed
        status = "provisional" if status_ceiling != "unresolved" else "unresolved"

        return StateDBDensity(
            base_density_zoning=base_zoning,
            base_density_specific_plan=base_sp,
            base_density_gp=base_gp,
            comparison_legs_evaluated=legs_evaluated,
            comparison_legs_unresolved=legs_unresolved,
            governing_base_density=governing_base,
            governing_base_source=governing_source,
            governing_base_confirmed=governing_base_confirmed,
            affordable_set_aside=set_aside,
            bonus_percentage=None,
            bonus_percentage_is_unlimited=True,
            bonus_units=None,
            total_units=None,
            bonus_rounding_rule="N/A (unlimited)",
            rental_or_for_sale="for_sale" if is_for_sale else "rental",
            is_100_pct_affordable=True,
            is_100_pct_affordable_confirmed=False,
            statutory_authority="AB 1287 / Gov. Code 65915(f)",
            status=status,
            issues=issues,
        )

    # ── Standard sliding scale ──────────────────────────────────────
    bonus_pct = _compute_bonus_pct(affordability, is_for_sale)

    if bonus_pct <= 0:
        issues.append(DensityIssue(
            step="STEP_5b_state_db",
            field="bonus_percentage",
            severity="warning",
            message="Affordability set-aside does not meet minimum thresholds for any density bonus.",
            action_required="Verify affordability percentages meet Gov. Code 65915 minimums.",
            confidence_impact="none",
        ))

    bonus_raw = governing_base * (bonus_pct / 100.0)
    bonus_units = _db_round(bonus_raw)
    total_units = governing_base + bonus_units

    # Final status: capped by upstream ceiling
    status = status_ceiling

    return StateDBDensity(
        base_density_zoning=base_zoning,
        base_density_specific_plan=base_sp,
        base_density_gp=base_gp,
        comparison_legs_evaluated=legs_evaluated,
        comparison_legs_unresolved=legs_unresolved,
        governing_base_density=governing_base,
        governing_base_source=governing_source,
        governing_base_confirmed=governing_base_confirmed,
        affordable_set_aside=set_aside,
        bonus_percentage=bonus_pct,
        bonus_percentage_is_unlimited=False,
        bonus_units=bonus_units,
        total_units=total_units,
        bonus_rounding_rule=_DB_ROUNDING_RULE,
        rental_or_for_sale="for_sale" if is_for_sale else "rental",
        is_100_pct_affordable=False,
        is_100_pct_affordable_confirmed=False,
        statutory_authority=f"Gov. Code 65915 ({bonus_pct:.0f}% bonus)",
        status=status,
        issues=issues,
    )
