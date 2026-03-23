"""Setback yard family rules (Step 2).

Returns parametric YardFormulas for the resolved yard family — never
collapsed yard-dimension numbers. The calc module (setback_edge_calc.py)
evaluates these formulas against actual lot_width, number_of_stories, and
per-edge inputs to produce numeric yard values.

═══════════════════════════════════════════════
FORMULA ASSUMPTIONS — READ BEFORE USE
═══════════════════════════════════════════════

Side yard lot-width increment:
    "+1 ft per 50 ft lot width over 50 ft" is drawn from the sprint spec
    directly. The specific LAMC subsection that authorizes this increment
    within each zone section has NOT been independently verified. All
    lot_width_increment_ft values carry a provisional note.

Side yard story increment:
    "+1 ft per story above 2nd" is in the sprint spec but explicitly labeled
    "check per zone section." story_increment_ft=1.0 / story_threshold=2 is
    implemented here as stated; the subsection basis must be confirmed before
    applying to permit calculations.

Rear yard alley reduction:
    alley_reduction_ft is intentionally left None. "Reduced at alley where
    applicable" is noted in the spec but the exact reduction amount and the
    governing provision (e.g., LAMC 12.21-G or zone-section equivalent) are
    not confirmed. The calc module must treat None as "alley reduction
    applicable, amount requires manual confirmation."

Front yard code minimum:
    base_ft is None for all front formulas. The code minimum is zone-specific
    and is NOT hardcoded here. The prevailing setback (LAMC 12.08-C or
    zone-section equivalent) governs in practice and is NEVER auto-calculated.
    prevailing_not_calculated=True is set on every front formula.

R5 formulas:
    R5 yard requirements are not explicitly detailed in the sprint spec. The
    R3/R4 formula shape is applied conservatively with a prominent provisional
    flag. Verify all R5 values against LAMC 12.12 before use.

RD zone formulas:
    RD zones are outside the primary sprint scope (multifamily/mixed-use).
    Formulas return base_ft=None with a manual-review note. Do not use
    RD output without verifying against LAMC 12.09.5 sub-zone tables.

MR zone formulas:
    MR1→R4 and MR2→R5 yard inheritance follows the density module's pattern.
    An info issue is appended noting that LAMC 12.17.5 residential yard
    inheritance should be verified before treating output as governing.
"""

from __future__ import annotations

from setback.models import (
    CMYardOption,
    SetbackAuthorityResult,
    SetbackIssue,
    YardFamilyResult,
    YardFormula,
)


# ─────────────────────────────────────────────────────────────────────────────
# Formula builders — private
# ─────────────────────────────────────────────────────────────────────────────

def _side_r3() -> YardFormula:
    return YardFormula(
        yard_type="side",
        base_ft=5.0,
        lot_width_increment_ft=1.0,
        lot_width_step_ft=50.0,
        lot_width_threshold_ft=50.0,
        story_increment_ft=1.0,     # PROVISIONAL — verify subsection in LAMC 12.10
        story_threshold=2,          # increments start at 3rd story (above 2nd)
        parametric=True,
        governing_section="LAMC 12.10",
        notes=(
            "5 ft base minimum. +1 ft per 50 ft of lot width over 50 ft. "
            "Story increment (+1 ft per story above 2nd) PROVISIONAL: "
            "verify the authorizing subsection in LAMC 12.10 before applying."
        ),
    )


def _side_r4() -> YardFormula:
    return YardFormula(
        yard_type="side",
        base_ft=5.0,
        lot_width_increment_ft=1.0,
        lot_width_step_ft=50.0,
        lot_width_threshold_ft=50.0,
        story_increment_ft=1.0,     # PROVISIONAL — verify subsection in LAMC 12.11
        story_threshold=2,
        parametric=True,
        governing_section="LAMC 12.11",
        notes=(
            "5 ft base minimum. +1 ft per 50 ft of lot width over 50 ft. "
            "Story increment (+1 ft per story above 2nd) PROVISIONAL: "
            "verify the authorizing subsection in LAMC 12.11 before applying."
        ),
    )


def _side_r5() -> YardFormula:
    """R5 side formula — fully provisional; R3/R4 shape applied conservatively."""
    return YardFormula(
        yard_type="side",
        base_ft=5.0,
        lot_width_increment_ft=1.0,
        lot_width_step_ft=50.0,
        lot_width_threshold_ft=50.0,
        story_increment_ft=1.0,     # PROVISIONAL
        story_threshold=2,
        parametric=True,
        governing_section="LAMC 12.12",
        notes=(
            "PROVISIONAL — R5 yard formula not explicitly defined in sprint spec. "
            "R3/R4 formula shape applied conservatively. "
            "Verify all values against LAMC 12.12 before use. "
            "Do not treat as governing without section confirmation."
        ),
    )


def _side_rd(rd_zone: str) -> YardFormula:
    """RD zone side formula — base_ft not hardcoded; manual review required."""
    return YardFormula(
        yard_type="side",
        base_ft=None,               # not hardcoded — varies by RD sub-zone
        parametric=True,
        governing_section="LAMC 12.09.5",
        notes=(
            f"PROVISIONAL — {rd_zone} yard formula not implemented (outside sprint scope). "
            "Consult LAMC 12.09.5 for the applicable sub-zone yard table."
        ),
    )


def _rear(governing_section: str, *, provisional: bool = False) -> YardFormula:
    """Standard residential rear yard formula.

    alley_reduction_ft is intentionally None — the reduction amount and
    governing provision must be confirmed before applying.
    """
    provisional_note = (
        " Formula shape not confirmed for this zone — use conservatively."
        if provisional else ""
    )
    return YardFormula(
        yard_type="rear",
        base_ft=15.0,
        alley_reduction_ft=None,    # PROVISIONAL — amount not confirmed; see module docstring
        parametric=True,
        governing_section=governing_section,
        notes=(
            "15 ft base rear yard (standard residential). "
            "Alley reduction applicable when rear lot line abuts a public alley, "
            "but reduction amount is NOT hardcoded — verify governing provision "
            "(LAMC 12.21-G or equivalent zone-section reference) before applying."
            + provisional_note
        ),
    )


def _front(governing_section: str) -> YardFormula:
    """Front yard formula — code minimum only; prevailing never calculated."""
    return YardFormula(
        yard_type="front",
        base_ft=None,               # code minimum NOT hardcoded; look up per section
        parametric=False,           # prevailing, not a parametric lot-width formula
        prevailing_not_calculated=True,
        governing_section=governing_section,
        notes=(
            "FRONT YARD: code minimum not hardcoded. "
            "The prevailing setback governs in practice (LAMC 12.08-C or "
            "zone-section equivalent) and is NOT calculated by this module. "
            f"Read the code minimum from {governing_section}. "
            "Flag all front edges for manual prevailing setback analysis."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Family formula map
# ─────────────────────────────────────────────────────────────────────────────
# Maps yard family name → (side_builder_fn, rear_section, front_section, provisional_rear)

_FAMILY_MAP: dict[str, tuple] = {
    "R3": (_side_r3, "LAMC 12.10", "LAMC 12.10", False),
    "R4": (_side_r4, "LAMC 12.11", "LAMC 12.11", False),
    "R5": (_side_r5, "LAMC 12.12", "LAMC 12.12", True),
}


def _formulas_for_family(
    family: str,
) -> tuple[YardFormula, YardFormula, YardFormula] | None:
    """Build (side, rear, front) for a named yard family. None if not in map."""
    spec = _FAMILY_MAP.get(family)
    if spec is None:
        return None
    side_fn, rear_section, front_section, provisional_rear = spec
    return side_fn(), _rear(rear_section, provisional=provisional_rear), _front(front_section)


# ─────────────────────────────────────────────────────────────────────────────
# CM split helper
# ─────────────────────────────────────────────────────────────────────────────

def _cm_options() -> list[CMYardOption]:
    """Return both CM yard option paths. Neither is auto-selected."""
    options: list[CMYardOption] = []
    for family, label in (
        ("R3", "R3 uses"),
        ("R4", "other residential at floor level"),
    ):
        formulas = _formulas_for_family(family)
        if formulas is None:
            raise RuntimeError(f"Expected R3/R4 in family map; '{family}' not found")
        side, rear, front = formulas
        res_section = "LAMC 12.10" if family == "R3" else "LAMC 12.11"
        options.append(CMYardOption(
            use_type_label=label,
            inherited_family=family,
            side_formula=side,
            rear_formula=rear,
            front_formula=front,
            governing_section=(
                f"LAMC 12.17.5 (CM zone) → {res_section} ({family} yard requirements)"
            ),
        ))
    return options


# ─────────────────────────────────────────────────────────────────────────────
# RAS subzone detection
# ─────────────────────────────────────────────────────────────────────────────

def _ras_residential_family(authority_result: SetbackAuthorityResult) -> str | None:
    """Determine the residential-above yard family for a RAS zone.

    RAS3 (LAMC 12.10.5) → residential above inherits R3.
    RAS4 (LAMC 12.11.5) → residential above inherits R4.

    Returns None if the subzone cannot be determined from governing_sections.
    """
    for sec in authority_result.governing_sections:
        if "12.10.5" in sec:
            return "R3"
        if "12.11.5" in sec:
            return "R4"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def get_yard_family_rules(
    authority_result: SetbackAuthorityResult,
) -> YardFamilyResult:
    """Return parametric yard-family rules for the resolved zone.

    Consumes the SetbackAuthorityResult from setback_authority.py.
    Does NOT evaluate formulas against project inputs — that is the
    responsibility of setback_edge_calc.py.

    Parameters not accepted here intentionally:
        lot_width, number_of_stories — these are calc-time parameters.
        The formula library stores the formula shape; the calc module
        applies it to actual project dimensions.

    Returns:
        YardFamilyResult with side/rear/front YardFormulas, or CM/RAS
        split structures where applicable. All formulas carry LAMC section
        citations and provisional flags where subsection basis is uncertain.
    """
    issues: list[SetbackIssue] = []

    # ── Early exit pass-through ──────────────────────────────────────────────
    if authority_result.early_exit.triggered:
        return YardFamilyResult(
            baseline_yard_family=authority_result.baseline_yard_family,
            governing_yard_family=authority_result.governing_yard_family,
            status="unresolved",
            requires_confirmation=True,
            issues=[SetbackIssue(
                step="STEP_2_yard_family",
                field="early_exit",
                severity="error",
                message=(
                    "Yard family resolution skipped: "
                    f"{authority_result.early_exit.reason}"
                ),
                action_required=(
                    "Resolve early exit condition before yard family rules "
                    "can be determined."
                ),
                confidence_impact="degrades_to_unresolved",
            )],
        )

    baseline = authority_result.baseline_yard_family
    governing = authority_result.governing_yard_family
    cm_split = authority_result.cm_split
    ras_split = authority_result.ras_split
    has_interrupters = bool(authority_result.authority_interrupters)

    # ── No zone resolved ─────────────────────────────────────────────────────
    if baseline is None and not cm_split and not ras_split:
        return YardFamilyResult(
            baseline_yard_family=None,
            governing_yard_family=None,
            status="unresolved",
            requires_confirmation=True,
            issues=[SetbackIssue(
                step="STEP_2_yard_family",
                field="baseline_yard_family",
                severity="error",
                message=(
                    "No baseline yard family resolved. "
                    "Cannot determine yard formulas."
                ),
                action_required=(
                    "Confirm base zone and resolve authority step before "
                    "proceeding to yard family rules."
                ),
                confidence_impact="degrades_to_unresolved",
            )],
        )

    # ── CM split ─────────────────────────────────────────────────────────────
    if cm_split:
        issues.append(SetbackIssue(
            step="STEP_2_yard_family",
            field="cm_split",
            severity="warning",
            message=(
                "CM zone: two yard option paths presented — neither is auto-selected. "
                "R3 uses: LAMC 12.17.5 → LAMC 12.10 (R3 yards, 5 ft base side). "
                "Other residential at floor level: LAMC 12.17.5 → LAMC 12.11 "
                "(R4 yards, 5 ft base side). "
                "Reviewer must confirm use type before applying either path."
            ),
            action_required=(
                "Confirm CM zone use type with reviewer. Select R3 or R4 yard "
                "family before proceeding to per-edge calculation."
            ),
            confidence_impact="degrades_to_unresolved",
        ))
        return YardFamilyResult(
            yard_family=None,
            baseline_yard_family=baseline,
            governing_yard_family=governing,
            cm_options=_cm_options(),
            requires_confirmation=True,
            status="split_condition",
            issues=issues,
        )

    # ── RAS split ────────────────────────────────────────────────────────────
    if ras_split:
        res_family = _ras_residential_family(authority_result)

        if res_family is None:
            # Cannot determine RAS subzone from governing sections
            issues.append(SetbackIssue(
                step="STEP_2_yard_family",
                field="ras_subzone",
                severity="error",
                message=(
                    "RAS zone detected but cannot determine subzone (RAS3 vs RAS4) "
                    "from governing sections. Cannot assign residential yard family."
                ),
                action_required=(
                    "Confirm whether zone is RAS3 (LAMC 12.10.5, R3 yards) or "
                    "RAS4 (LAMC 12.11.5, R4 yards) before proceeding."
                ),
                confidence_impact="degrades_to_unresolved",
            ))
            return YardFamilyResult(
                yard_family=None,
                baseline_yard_family=baseline,
                governing_yard_family=governing,
                requires_confirmation=True,
                status="unresolved",
                issues=issues,
            )

        res_section = "LAMC 12.10.5" if res_family == "R3" else "LAMC 12.11.5"
        formulas = _formulas_for_family(res_family)
        if formulas is None:
            raise RuntimeError(
                f"Expected R3/R4 in family map for RAS; '{res_family}' not found"
            )
        side_formula, rear_formula, front_formula = formulas

        issues.append(SetbackIssue(
            step="STEP_2_yard_family",
            field="ras_split",
            severity="warning",
            message=(
                f"RAS zone ({res_section}): residential-above portion inherits "
                f"{res_family} yard rules — formulas returned in side/rear/front fields. "
                "Ground-floor commercial yard requirements are NOT resolved here. "
                "ras_ground_floor_formula is null pending separate manual review."
            ),
            action_required=(
                "Confirm RAS ground-floor commercial yard requirements separately. "
                "Do not apply residential yard formulas to the commercial ground-floor portion."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        return YardFamilyResult(
            yard_family=None,               # split — no single family name applies
            baseline_yard_family=baseline,
            governing_yard_family=governing,
            side_formula=side_formula,
            rear_formula=rear_formula,
            front_formula=front_formula,
            ras_ground_floor_formula=None,  # not resolved this step
            requires_confirmation=True,
            status="split_condition",
            issues=issues,
        )

    # ── Standard single-family resolution ────────────────────────────────────
    # Use governing family when confirmed; fall back to baseline when interrupters
    # are present. Status and requires_confirmation reflect which path was taken.
    active_family = governing if governing is not None else baseline

    # RD zones — zone resolves but yard formula library is not implemented
    if active_family and active_family.startswith("RD"):
        issues.append(SetbackIssue(
            step="STEP_2_yard_family",
            field="rd_zone",
            severity="warning",
            message=(
                f"{active_family} is outside the primary sprint scope "
                "(multifamily / mixed-use). Yard formula not implemented. "
                "Returned formulas have base_ft=None and must not be used "
                "for calculations without manual verification."
            ),
            action_required=(
                f"Manually verify {active_family} yard requirements against "
                "LAMC 12.09.5 sub-zone tables before using any output."
            ),
            confidence_impact="degrades_to_provisional",
        ))
        return YardFamilyResult(
            yard_family=active_family,
            baseline_yard_family=baseline,
            governing_yard_family=governing,
            side_formula=_side_rd(active_family),
            rear_formula=YardFormula(
                yard_type="rear",
                base_ft=None,
                governing_section="LAMC 12.09.5",
                notes=(
                    f"PROVISIONAL — {active_family} rear yard not implemented. "
                    "Verify against LAMC 12.09.5."
                ),
            ),
            front_formula=_front("LAMC 12.09.5"),
            requires_confirmation=True,
            status="provisional",
            issues=issues,
        )

    # M-family (MR zones) — informational note on inherited residential yards
    if authority_result.code_family == "M":
        issues.append(SetbackIssue(
            step="STEP_2_yard_family",
            field="m_zone_inherited_yards",
            severity="info",
            message=(
                f"MR zone: residential yard rules follow inherited {active_family} "
                "family per density module inheritance pattern. "
                "Verify that LAMC 12.17.5 explicitly directs residential uses "
                f"to comply with {active_family} yard requirements before treating "
                "this output as governing."
            ),
            action_required=(
                f"Confirm MR zone → {active_family} yard inheritance against "
                "LAMC 12.17.5 before use in permit calculations."
            ),
            confidence_impact="none",
        ))

    # Build formulas from the family map
    formulas = _formulas_for_family(active_family)
    if formulas is None:
        return YardFamilyResult(
            baseline_yard_family=baseline,
            governing_yard_family=governing,
            status="unresolved",
            requires_confirmation=True,
            issues=[SetbackIssue(
                step="STEP_2_yard_family",
                field="yard_family",
                severity="error",
                message=(
                    f"Yard family '{active_family}' is not in the formula library. "
                    "Cannot produce parametric formulas."
                ),
                action_required=(
                    f"Manually determine yard formulas for '{active_family}' "
                    "and add them to the formula library or flag for manual review."
                ),
                confidence_impact="degrades_to_unresolved",
            )],
        )

    side_formula, rear_formula, front_formula = formulas

    # ── Status: confirmed vs. provisional ────────────────────────────────────
    # Confirmed: governing family is set (no unresolved interrupters) and
    #            the formula came from a fully-implemented family (not RD/R5).
    # Provisional: only baseline available due to interrupters, or R5/MR zone.
    is_provisional_family = (active_family == "R5") or (authority_result.code_family == "M")

    if governing is not None and not has_interrupters and not is_provisional_family:
        status = "confirmed"
    else:
        status = "provisional"
        if has_interrupters:
            issues.append(SetbackIssue(
                step="STEP_2_yard_family",
                field="authority_interrupters",
                severity="warning",
                message=(
                    f"Yard formulas derived from baseline yard family '{baseline}'. "
                    "Governing family is not confirmed because unresolved authority "
                    "interrupters are present — an overlay may modify these requirements."
                ),
                action_required=(
                    "Resolve all authority interrupters before treating these "
                    "yard formulas as governing."
                ),
                confidence_impact="degrades_to_provisional",
            ))

    return YardFamilyResult(
        yard_family=active_family,
        baseline_yard_family=baseline,
        governing_yard_family=governing,
        side_formula=side_formula,
        rear_formula=rear_formula,
        front_formula=front_formula,
        requires_confirmation=has_interrupters or is_provisional_family,
        status=status,
        issues=issues,
    )
