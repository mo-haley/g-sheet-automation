"""State Density Bonus parking ratios (Parking Step 5b).

Government Code 65915(p) parking maximums.

Statutory coverage disclosure:
  This module currently implements:
    - Standard bedroom-based State DB parking ratios per 65915(p)(1)
    - 100% affordable 0.5 spaces/unit path per 65915(p)(2)/(p)(3)
  This module does NOT yet fully adjudicate:
    - Special lower-cap conditions for senior housing, supportive housing,
      or other qualifying categories under 65915(p)(2)/(p)(3) beyond 100% affordable
    - Parking reductions for projects near transit under 65915(p)(4)
    - Land-dedication-in-lieu provisions
    - Filing-date-dependent statutory amendments
  Partial coverage means results should not be read as exhaustive statutory
  determinations. Reviewers should verify against the operative statute at filing.

Rounding convention:
  This module applies math.ceil() to the sum of per-unit-type parking
  to produce total_required. This is a local implementation convention.
"""

from __future__ import annotations

import math

from models.project import Project, UnitType
from parking.models import ParkingIssue, StateDBParking, StateDBUnitParkingLine

# Gov. Code 65915(p) parking ratios by bedroom count.
# These are the standard ratios from 65915(p)(1). Verify against the
# operative statute at filing — rates have been amended multiple times.
STATE_DB_RATES: dict[str, float] = {
    "studio": 1.0,
    "Studio": 1.0,
    "0BR": 1.0,
    "1BR": 1.0,
    "2BR": 1.5,
    "3BR": 1.5,
    "4BR": 2.5,
}

# For 100% affordable or qualifying projects per 65915(p)(2)/(p)(3)
STATE_DB_100_AFFORDABLE_RATE = 0.5

# Statutory branches this module evaluates vs does not evaluate
_BRANCHES_EVALUATED = [
    "65915(p)(1) standard bedroom-based ratios",
    "65915(p)(2)/(p)(3) 100% affordable 0.5/unit",
]
_BRANCHES_NOT_EVALUATED = [
    "65915(p)(2)/(p)(3) senior/supportive/special qualifying categories",
    "65915(p)(4) transit proximity further reduction",
    "land-dedication-in-lieu provisions",
    "filing-date-dependent statutory amendments",
]


def _get_state_db_rate(unit_type: UnitType) -> tuple[float, bool]:
    """Look up State DB parking rate by unit type label.

    Returns:
        (rate, was_fallback) — was_fallback is True when the label wasn't
        found in the rate table and we fell back to bedroom-count inference.
    """
    if unit_type.label in STATE_DB_RATES:
        return STATE_DB_RATES[unit_type.label], False

    # Fallback: infer from bedroom count. This is a heuristic, not an
    # authoritative mapping.
    if unit_type.bedrooms == 0:
        return 1.0, True
    elif unit_type.bedrooms == 1:
        return 1.0, True
    elif unit_type.bedrooms <= 3:
        return 1.5, True
    else:
        return 2.5, True


def compute_state_db_parking(
    project: Project,
    total_units: int,
    is_100_pct_affordable: bool = False,
    is_100_pct_affordable_confirmed: bool = False,
) -> StateDBParking:
    """STEP 5b: Compute State DB parking ratios.

    Args:
        project: Project data with unit mix.
        total_units: Total unit count (from density module).
        is_100_pct_affordable: Whether project plans/intends 100% affordable.
        is_100_pct_affordable_confirmed: Whether 100% affordable treatment
            has been explicitly confirmed for parking purposes.
    """
    issues: list[ParkingIssue] = []
    lines: list[StateDBUnitParkingLine] = []
    used_default = False
    had_rate_fallback = False

    # ── 100% affordable path ────────────────────────────────────────
    if is_100_pct_affordable:
        spaces = math.ceil(total_units * STATE_DB_100_AFFORDABLE_RATE)
        lines.append(StateDBUnitParkingLine(
            unit_type="All (100% affordable, 0.5/unit)",
            count=total_units,
            rate=STATE_DB_100_AFFORDABLE_RATE,
            spaces=spaces,
        ))

        if not is_100_pct_affordable_confirmed:
            issues.append(ParkingIssue(
                step="STEP_5b_state_db_parking",
                field="is_100_pct_affordable",
                severity="warning",
                message=(
                    "100% affordable parking treatment (0.5 spaces/unit) applied as "
                    "provisional working assumption based on project affordability plan. "
                    "Treatment is not yet explicitly confirmed. Final eligibility for "
                    "65915(p)(2)/(p)(3) reduced rate should be verified with HCIDLA or counsel."
                ),
                action_required="Confirm 100% affordable treatment eligibility for State DB parking.",
                confidence_impact="degrades_to_provisional",
            ))

        issues.append(ParkingIssue(
            step="STEP_5b_state_db_parking",
            field="statutory_coverage",
            severity="info",
            message=(
                "This module evaluates standard 65915(p) ratios and the "
                "100% affordable 0.5/unit path. It does not adjudicate "
                "senior/supportive housing special categories, transit proximity "
                "further reductions, or filing-date-dependent amendments."
            ),
            confidence_impact="none",
        ))

        return StateDBParking(
            unit_mix=lines,
            total_required=spaces,
            statutory_section="Gov. Code 65915(p)(2)/(p)(3)",
            is_100_pct_affordable=True,
            is_100_pct_affordable_confirmed=is_100_pct_affordable_confirmed,
            used_default_unit_mix_assumption=False,
            total_rounding_convention="ceil_per_unit_sum",
            statutory_branches_evaluated=list(_BRANCHES_EVALUATED),
            statutory_branches_not_evaluated=list(_BRANCHES_NOT_EVALUATED),
            status="provisional",
            issues=issues,
        )

    # ── Standard bedroom-based ratios ───────────────────────────────
    if project.unit_mix:
        for ut in project.unit_mix:
            rate, was_fallback = _get_state_db_rate(ut)
            if was_fallback:
                had_rate_fallback = True
                issues.append(ParkingIssue(
                    step="STEP_5b_state_db_parking",
                    field="unit_rate",
                    severity="info",
                    message=(
                        f"Unit type '{ut.label}' not found in State DB rate table. "
                        f"Inferred rate {rate} from bedroom count ({ut.bedrooms} BR). "
                        f"This is a heuristic, not an authoritative statutory lookup."
                    ),
                    action_required=f"Confirm State DB parking rate for unit type '{ut.label}'.",
                    confidence_impact="degrades_to_provisional",
                ))
            spaces = ut.count * rate
            lines.append(StateDBUnitParkingLine(
                unit_type=ut.label,
                count=ut.count,
                rate=rate,
                spaces=spaces,
            ))
    else:
        # No unit mix: internal fallback assumption.
        used_default = True
        issues.append(ParkingIssue(
            step="STEP_5b_state_db_parking",
            field="unit_mix",
            severity="warning",
            message=(
                f"No unit mix provided. Using internal fallback assumption: "
                f"{total_units} units at 1.0 space/unit (studio/1BR default). "
                f"This is not a confirmed State DB parking determination — actual "
                f"parking depends on unit bedroom counts which are unknown."
            ),
            action_required="Provide unit mix with bedroom counts for accurate State DB parking.",
            confidence_impact="degrades_to_provisional",
        ))
        lines.append(StateDBUnitParkingLine(
            unit_type="Unknown (fallback assumption)",
            count=total_units,
            rate=1.0,
            spaces=float(total_units),
        ))

    total = math.ceil(sum(line.spaces for line in lines))

    # Statutory coverage disclosure
    issues.append(ParkingIssue(
        step="STEP_5b_state_db_parking",
        field="statutory_coverage",
        severity="info",
        message=(
            "This module evaluates standard 65915(p) bedroom-based ratios. "
            "It does not adjudicate senior/supportive housing special categories, "
            "transit proximity further reductions, 100% affordable sub-paths "
            "(project is not flagged as 100% affordable), or filing-date-dependent amendments."
        ),
        confidence_impact="none",
    ))

    # Status: provisional if any fallback/heuristic was used
    status = "provisional"

    return StateDBParking(
        unit_mix=lines,
        total_required=total,
        statutory_section="Gov. Code 65915(p)",
        is_100_pct_affordable=False,
        is_100_pct_affordable_confirmed=False,
        used_default_unit_mix_assumption=used_default,
        total_rounding_convention="ceil_per_unit_sum",
        statutory_branches_evaluated=list(_BRANCHES_EVALUATED),
        statutory_branches_not_evaluated=list(_BRANCHES_NOT_EVALUATED),
        status=status,
        issues=issues,
    )
