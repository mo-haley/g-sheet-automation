"""AB 1287 stackable density bonus calculator (Gov. Code 65915(v)).

AB 1287 (2022) allows a project that qualifies for the maximum 50% primary
density bonus to stack an additional ("stackable") bonus by providing a
SEPARATE affordable set-aside above the primary threshold.

Primary bonus eligibility gate (project must already qualify for max 50%):
    Rental or for-sale: ≥15% VLI, OR
    Rental or for-sale: ≥24% LI, OR
    For-sale only:      ≥44% Moderate

Stackable bonus set-aside (additional units beyond the primary threshold):
    VLI stackable (rental or for-sale):  5–10% additional VLI → bonus table
    Moderate stackable (for-sale only):  5–15% additional moderate → bonus table

Rounding:
    Primary and stackable bonuses are rounded SEPARATELY.
    base_units_raw (lot_area / sf_per_du, the pre-rounded float) is reused
    for both rounding steps so that each bonus is computed against the same
    continuous base rather than against a pre-rounded integer.
    Total = _db_round(base_units_raw) + primary_bonus_units + stackable_bonus_units.

Incentive counts (GC 65915(d)(2)(A) as amended by AB 1287):
    Mixed-income with ≥16% VLI or ≥45% moderate for-sale: 4 incentives
    100% affordable:                                        5 incentives
    Standard sliding scale (below AB 1287 thresholds):     per standard table
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from models.project import AffordabilityPlan


# ── Rounding (same convention as density_state_db_calc) ──────────────────────

def _db_round(raw: float) -> int:
    """Round DB bonus units per local convention (LAMC 12.22 A.18 floor except 0.5 up)."""
    fraction = raw - math.floor(raw)
    if abs(fraction - 0.5) < 1e-9:
        return math.ceil(raw)
    return math.floor(raw)


# ── Eligibility thresholds ────────────────────────────────────────────────────

# Primary bonus must reach max (50%) — these are the qualifying set-aside floors.
_PRIMARY_MAX_VLI_THRESHOLD = 15    # % VLI (rental or for-sale)
_PRIMARY_MAX_LI_THRESHOLD = 24     # % LI  (rental or for-sale)
_PRIMARY_MAX_MOD_THRESHOLD = 44    # % Moderate (for-sale only)

# Stackable set-aside range (additional, above primary threshold)
_STACKABLE_VLI_MIN = 5
_STACKABLE_VLI_MAX = 10
_STACKABLE_MOD_MIN = 5
_STACKABLE_MOD_MAX = 15

# ── Stackable bonus lookup tables ─────────────────────────────────────────────
# Key: integer stackable set-aside percentage. Value: additional bonus percentage.

_VLI_STACKABLE_TABLE: dict[int, float] = {
    5:  20.00,
    6:  23.75,
    7:  27.50,
    8:  31.25,
    9:  35.00,
    10: 38.75,
}

_MOD_STACKABLE_TABLE: dict[int, float] = {
    5:  20.00,
    6:  22.50,
    7:  25.00,
    8:  27.50,
    9:  30.00,
    10: 32.50,
    11: 35.00,
    12: 38.75,
    13: 42.50,
    14: 46.25,
    15: 50.00,
}

# ── Incentive count thresholds ────────────────────────────────────────────────

_INCENTIVE_4_VLI_THRESHOLD = 16      # % VLI → 4 incentives (mixed-income)
_INCENTIVE_4_MOD_THRESHOLD = 45      # % Moderate (for-sale) → 4 incentives


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class AB1287Result:
    """Structured output from compute_ab1287."""
    ab1287_eligible: bool = False
    ab1287_stack_bonus_pct: float | None = None
    ab1287_stack_units: int | None = None
    # Total = base + primary_bonus + stackable_bonus (None if base_units_raw not provided)
    ab1287_total_units: int | None = None
    ab1287_statutory_authority: str | None = None
    ab1287_incentives_available: int | None = None
    # Human-readable eligibility reason when not eligible
    ineligibility_reason: str | None = None


# ── Public function ───────────────────────────────────────────────────────────

def compute_ab1287(
    affordability: AffordabilityPlan,
    is_for_sale: bool,
    primary_bonus_pct: float,
    base_units_raw: float | None = None,
) -> AB1287Result:
    """Compute AB 1287 stackable density bonus.

    Args:
        affordability:      Project affordability plan.
        is_for_sale:        True for ownership projects.
        primary_bonus_pct:  Primary bonus percentage already computed (0–50).
        base_units_raw:     Governing base density as a pre-rounded float
                            (lot_area / sf_per_du). When provided, stackable
                            bonus units and total units are computed with
                            independent rounding per GC 65915(v).
                            When None, units are omitted (percentage only).

    Returns:
        AB1287Result with eligibility, stackable bonus %, units (if computable),
        statutory authority string, and incentive count.
    """
    vli_pct = affordability.vli_pct
    li_pct = affordability.li_pct
    mod_pct = affordability.moderate_pct

    # ── Eligibility gate ─────────────────────────────────────────────────────
    # Project must already qualify for max 50% primary bonus.
    qualifies_vli = vli_pct >= _PRIMARY_MAX_VLI_THRESHOLD
    qualifies_li = li_pct >= _PRIMARY_MAX_LI_THRESHOLD
    qualifies_mod = is_for_sale and mod_pct >= _PRIMARY_MAX_MOD_THRESHOLD

    if not (qualifies_vli or qualifies_li or qualifies_mod):
        # Build a concise explanation.
        reasons = []
        if vli_pct > 0:
            reasons.append(
                f"VLI {vli_pct:.0f}% < {_PRIMARY_MAX_VLI_THRESHOLD}% required"
            )
        if li_pct > 0:
            reasons.append(
                f"LI {li_pct:.0f}% < {_PRIMARY_MAX_LI_THRESHOLD}% required"
            )
        if is_for_sale and mod_pct > 0:
            reasons.append(
                f"Moderate {mod_pct:.0f}% < {_PRIMARY_MAX_MOD_THRESHOLD}% required (for-sale)"
            )
        if not reasons:
            reasons.append(
                f"Primary threshold not met — requires ≥{_PRIMARY_MAX_VLI_THRESHOLD}% VLI, "
                f"≥{_PRIMARY_MAX_LI_THRESHOLD}% LI, "
                f"or ≥{_PRIMARY_MAX_MOD_THRESHOLD}% Moderate (for-sale)"
            )
        return AB1287Result(
            ab1287_eligible=False,
            ineligibility_reason="; ".join(reasons),
            ab1287_incentives_available=_compute_incentive_count(
                vli_pct, mod_pct, is_for_sale, is_100_pct=False
            ),
        )

    # ── Stackable set-aside computation ──────────────────────────────────────
    # Stackable VLI = surplus VLI above primary max threshold, capped at table range.
    stackable_vli: float = 0.0
    if qualifies_vli:
        stackable_vli = max(0.0, min(vli_pct - _PRIMARY_MAX_VLI_THRESHOLD, _STACKABLE_VLI_MAX))

    # Stackable Moderate (for-sale only) = surplus above primary max threshold.
    stackable_mod: float = 0.0
    if qualifies_mod:
        stackable_mod = max(0.0, min(mod_pct - _PRIMARY_MAX_MOD_THRESHOLD, _STACKABLE_MOD_MAX))

    # ── Best stackable bonus percentage ──────────────────────────────────────
    best_stack_pct: float | None = None
    best_stackable_source: str | None = None

    vli_lookup_pct = int(math.floor(stackable_vli))
    if vli_lookup_pct >= _STACKABLE_VLI_MIN and vli_lookup_pct in _VLI_STACKABLE_TABLE:
        vli_stack = _VLI_STACKABLE_TABLE[vli_lookup_pct]
        if best_stack_pct is None or vli_stack > best_stack_pct:
            best_stack_pct = vli_stack
            best_stackable_source = "vli"

    mod_lookup_pct = int(math.floor(stackable_mod))
    if is_for_sale and mod_lookup_pct >= _STACKABLE_MOD_MIN and mod_lookup_pct in _MOD_STACKABLE_TABLE:
        mod_stack = _MOD_STACKABLE_TABLE[mod_lookup_pct]
        if best_stack_pct is None or mod_stack > best_stack_pct:
            best_stack_pct = mod_stack
            best_stackable_source = "moderate"

    if best_stack_pct is None:
        # Eligible but surplus set-aside is below the stackable minimum.
        surplus_note = ""
        if qualifies_vli:
            surplus_note = (
                f"VLI surplus above {_PRIMARY_MAX_VLI_THRESHOLD}% = {stackable_vli:.1f}% "
                f"(min {_STACKABLE_VLI_MIN}% required for stacking)"
            )
        elif qualifies_mod:
            surplus_note = (
                f"Moderate surplus above {_PRIMARY_MAX_MOD_THRESHOLD}% = {stackable_mod:.1f}% "
                f"(min {_STACKABLE_MOD_MIN}% required for stacking)"
            )
        return AB1287Result(
            ab1287_eligible=True,
            ineligibility_reason=f"Eligible for stacking but no qualifying stackable set-aside. {surplus_note}",
            ab1287_incentives_available=_compute_incentive_count(
                vli_pct, mod_pct, is_for_sale, is_100_pct=False
            ),
        )

    # ── Unit computation ─────────────────────────────────────────────────────
    stack_units: int | None = None
    total_units: int | None = None

    if base_units_raw is not None and base_units_raw > 0:
        base_units_int = _db_round(base_units_raw)
        primary_bonus_units = _db_round(base_units_raw * (primary_bonus_pct / 100.0))
        stack_units = _db_round(base_units_raw * (best_stack_pct / 100.0))
        total_units = base_units_int + primary_bonus_units + stack_units

    incentives = _compute_incentive_count(vli_pct, mod_pct, is_for_sale, is_100_pct=False)

    return AB1287Result(
        ab1287_eligible=True,
        ab1287_stack_bonus_pct=best_stack_pct,
        ab1287_stack_units=stack_units,
        ab1287_total_units=total_units,
        ab1287_statutory_authority="Gov. Code §65915(v) [AB 1287, 2022]",
        ab1287_incentives_available=incentives,
    )


def _compute_incentive_count(
    vli_pct: float,
    mod_pct: float,
    is_for_sale: bool,
    is_100_pct: bool,
) -> int:
    """Return the AB 1287 incentive count for this project.

    5 for 100% affordable projects.
    4 for mixed-income with ≥16% VLI or ≥45% moderate (for-sale).
    Standard count (not computed here) otherwise.
    """
    if is_100_pct:
        return 5
    if vli_pct >= _INCENTIVE_4_VLI_THRESHOLD:
        return 4
    if is_for_sale and mod_pct >= _INCENTIVE_4_MOD_THRESHOLD:
        return 4
    # Below AB 1287 thresholds — standard incentive count governed by primary bonus level.
    # Return None to indicate the standard table applies (not overridden by AB 1287).
    return 0  # caller should treat 0 as "standard table applies"
