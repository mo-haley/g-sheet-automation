"""Setback edge yard calculation (Step 4 of 6).

Evaluates the baseline → use-adjusted → adjacency-adjusted → governing
yard chain for each classified edge. Returns per-edge EdgeResult objects.

Does NOT assemble final project-level status — that is setback_status.py.

═══════════════════════════════════════════════
CALCULATION POSTURE
═══════════════════════════════════════════════

Uncertainty propagates through the chain rather than being collapsed:

  - base_ft is None        → baseline_yard_ft is None; do not substitute zero
  - lot_width not provided → lot-width increment omitted; result provisional
  - story increment fires  → result provisional (subsection unconfirmed)
  - story increment does not fire (stories ≤ threshold) → no provisional contribution
  - adjacency unknown      → adjacency_status="not_provided"; no assumption
  - adjacency more restrictive, amount unknown → governing_yard_ft = None; flagged
  - alley_reduction_ft=None → conservative: full 15 ft maintained; flagged
  - CM split unresolved    → EdgeResult.status="split_condition"; no yard computed
  - RAS ground-floor commercial unresolved → noted; residential formula applied
  - through-lot dual front → both front edges return prevailing_setback_flag=True
  - manual_confirm edge    → result.status at most "provisional"

═══════════════════════════════════════════════
ADU SETBACK
═══════════════════════════════════════════════

adu_override_yard_ft=4.0 on qualifying side/rear edges (LAMC 12.22-C-27).
Applies to the ADU BUILDING PORTION ONLY. Primary building yard is governed
by the baseline → adjusted chain. Never applied to front or side_street_side.
"""

from __future__ import annotations

import re

from setback.models import (
    AdjustmentStep,
    ClassifiedEdge,
    EdgeInput,
    EdgeResult,
    SetbackAuthorityResult,
    SetbackIssue,
    SetbackProjectInputs,
    YardFamilyResult,
    YardFormula,
)


# ADU setback per LAMC 12.22-C-27 (ministerial ADU standards)
_ADU_SETBACK_FT: float = 4.0
_ADU_SETBACK_SECTION: str = "LAMC 12.22-C-27"

# Adjacency zones that are typically more restrictive than multifamily/commercial.
# Used to detect when an adjacency yard increase flag is warranted.
_RESTRICTIVE_ZONE_BASES: frozenset[str] = frozenset({
    "A1", "A2",
    "R1", "R2",
    "RD1.5", "RD2", "RD3", "RD4", "RD5", "RD6",
})


# ─────────────────────────────────────────────────────────────────────────────
# Zone helpers
# ─────────────────────────────────────────────────────────────────────────────

def _base_zone(zone_str: str) -> str:
    """Strip height-district suffix for zone comparison (e.g. 'R1-1XL' → 'R1')."""
    return re.split(r"[-/\s]", zone_str.strip())[0].upper()


def _is_more_restrictive_adjacency(adj_zone: str, project_code_family: str | None) -> bool:
    """Return True when adj_zone is more restrictive than the project's code family.

    Does not attempt to compute the exact yard difference — callers that
    receive True should flag the edge for manual adjacency review.
    """
    base = _base_zone(adj_zone)
    if base in _RESTRICTIVE_ZONE_BASES:
        return True
    # R3 is more restrictive than R4 / R5 / C / M for projects in those families
    if base == "R3" and project_code_family in ("R4", "R5", "C", "M", "CM", "RAS"):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Formula selection
# ─────────────────────────────────────────────────────────────────────────────

def _get_formula(classification: str, yard_family_result: YardFamilyResult) -> YardFormula | None:
    """Return the applicable YardFormula for the classified edge role."""
    if classification in ("side", "side_street_side"):
        return yard_family_result.side_formula
    if classification == "rear":
        return yard_family_result.rear_formula
    if classification == "front":
        return yard_family_result.front_formula
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Formula evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_side_formula(
    formula: YardFormula,
    lot_width: float | None,
    number_of_stories: int | None,
) -> tuple[float | None, list[AdjustmentStep], bool]:
    """Evaluate a side (or side_street_side) yard formula against project inputs.

    Returns:
        (value, chain_steps, is_provisional)
        value          — computed yard from available inputs; None if base_ft is None
        chain_steps    — each calculation step for the adjustment_chain
        is_provisional — True when any parameter was missing or when the story
                         increment fired (subsection unconfirmed per formula notes)
    """
    if formula.base_ft is None:
        return None, [], True

    value: float = formula.base_ft
    steps: list[AdjustmentStep] = [AdjustmentStep(
        adjustment=f"base minimum {value:.1f} ft",
        reason="Zone base side yard minimum",
        section=formula.governing_section,
        value_ft=value,
    )]
    is_provisional = False

    # ── Lot-width increment ──────────────────────────────────────────────────
    lw_inc = formula.lot_width_increment_ft
    lw_step = formula.lot_width_step_ft
    lw_thresh = formula.lot_width_threshold_ft

    if lw_inc is not None and lw_step is not None and lw_thresh is not None:
        if lot_width is None:
            is_provisional = True
            steps.append(AdjustmentStep(
                adjustment="lot-width increment: not evaluated",
                reason=(
                    f"lot_width not provided — cannot compute lot-width increment. "
                    f"Formula: +{lw_inc:.0f} ft per {lw_step:.0f} ft of lot width "
                    f"over {lw_thresh:.0f} ft. Provide lot_width to complete baseline."
                ),
                section=formula.governing_section,
                value_ft=None,
            ))
        elif lot_width > lw_thresh:
            over = lot_width - lw_thresh
            increments = int(over // lw_step)
            addition = increments * lw_inc
            if addition > 0:
                value += addition
                steps.append(AdjustmentStep(
                    adjustment=f"+{addition:.1f} ft lot-width increment",
                    reason=(
                        f"Lot width {lot_width:.0f} ft; threshold {lw_thresh:.0f} ft — "
                        f"{over:.0f} ft over → {increments} × {lw_step:.0f} ft "
                        f"= +{addition:.1f} ft"
                    ),
                    section=formula.governing_section,
                    value_ft=value,
                ))
        # else: lot_width ≤ threshold — increment does not fire; no step added

    # ── Story increment (PROVISIONAL when fired) ─────────────────────────────
    s_inc = formula.story_increment_ft
    s_thresh = formula.story_threshold

    if s_inc is not None and s_thresh is not None:
        if number_of_stories is None:
            is_provisional = True
            steps.append(AdjustmentStep(
                adjustment="story increment: not evaluated",
                reason=(
                    f"number_of_stories not provided — cannot determine whether "
                    f"story increment applies. "
                    f"Formula: +{s_inc:.0f} ft per story above {s_thresh}."
                ),
                section=formula.governing_section,
                value_ft=None,
            ))
        elif number_of_stories > s_thresh:
            extra = number_of_stories - s_thresh
            addition = extra * s_inc
            # Apply cap if story_increment_max_ft is set (e.g. "not to exceed 16 ft")
            cap = formula.story_increment_max_ft
            if cap is not None and value + addition > cap:
                addition = max(0.0, cap - value)
            value += addition
            is_provisional = True   # story increment is always provisional when fired
            cap_note = f" (capped at {cap:.0f} ft)" if cap is not None else ""
            steps.append(AdjustmentStep(
                adjustment=f"+{addition:.1f} ft story increment{cap_note}",
                reason=(
                    f"{number_of_stories} stories exceeds {s_thresh}-story threshold "
                    f"by {extra} → {extra} × {s_inc:.0f} ft = +{extra * s_inc:.1f} ft"
                    f"{f', capped to {cap:.0f} ft total' if cap is not None and value >= cap else ''}. "
                    f"Per {formula.governing_section} / Table 1b."
                ),
                section=formula.governing_section,
                value_ft=value,
            ))
        # else: stories ≤ threshold — increment does not fire; no provisional concern

    return value, steps, is_provisional


def _evaluate_rear_formula(
    formula: YardFormula,
    is_alley_edge: bool,
    number_of_stories: int | None = None,
) -> tuple[float | None, list[AdjustmentStep], bool]:
    """Evaluate a rear yard formula.

    R4/R5 zones add +1 ft per story above 3rd, capped at 20 ft (Table 1b).

    When the rear lot line abuts an alley (is_alley_edge=True) and
    alley_reduction_ft is None (amount not confirmed), the full base_ft
    is returned conservatively — the reduction is NOT applied until the
    governing provision is confirmed.
    """
    if formula.base_ft is None:
        return None, [], True

    value: float = formula.base_ft
    steps: list[AdjustmentStep] = [AdjustmentStep(
        adjustment=f"base minimum {value:.1f} ft",
        reason="Standard residential rear yard minimum",
        section=formula.governing_section,
        value_ft=value,
    )]
    is_provisional = False

    # ── Story increment (R4/R5 rear: +1 ft per story above 3rd, max 20 ft) ──
    s_inc = formula.story_increment_ft
    s_thresh = formula.story_threshold

    if s_inc is not None and s_thresh is not None:
        if number_of_stories is None:
            is_provisional = True
            steps.append(AdjustmentStep(
                adjustment="rear story increment: not evaluated",
                reason=(
                    f"number_of_stories not provided — cannot determine whether "
                    f"rear yard story increment applies. "
                    f"Formula: +{s_inc:.0f} ft per story above {s_thresh}."
                ),
                section=formula.governing_section,
                value_ft=None,
            ))
        elif number_of_stories > s_thresh:
            extra = number_of_stories - s_thresh
            addition = extra * s_inc
            cap = formula.story_increment_max_ft
            if cap is not None and value + addition > cap:
                addition = max(0.0, cap - value)
            value += addition
            cap_note = f" (capped at {cap:.0f} ft)" if cap is not None else ""
            steps.append(AdjustmentStep(
                adjustment=f"+{addition:.1f} ft rear story increment{cap_note}",
                reason=(
                    f"{number_of_stories} stories exceeds {s_thresh}-story threshold "
                    f"by {extra}. Per {formula.governing_section} / Table 1b."
                ),
                section=formula.governing_section,
                value_ft=value,
            ))

    if is_alley_edge:
        if formula.alley_reduction_ft is not None:
            value -= formula.alley_reduction_ft
            steps.append(AdjustmentStep(
                adjustment=f"-{formula.alley_reduction_ft:.1f} ft alley reduction",
                reason=(
                    f"Rear lot line abuts public alley — "
                    f"{formula.alley_reduction_ft:.1f} ft reduction applied "
                    f"per {formula.governing_section}."
                ),
                section=formula.governing_section,
                value_ft=value,
            ))
        else:
            # alley_reduction_ft is None: amount not confirmed
            is_provisional = True
            steps.append(AdjustmentStep(
                adjustment="alley reduction: amount not confirmed",
                reason=(
                    "Rear lot line abuts public alley — reduction may apply, "
                    "but the reduction amount is NOT confirmed. "
                    "Conservative: full 15 ft maintained until verified. "
                    "Verify governing provision (LAMC 12.21-G or equivalent) "
                    f"before applying any reduction."
                ),
                section=formula.governing_section,
                value_ft=None,
            ))

    return value, steps, is_provisional


# ─────────────────────────────────────────────────────────────────────────────
# Adjustment steps
# ─────────────────────────────────────────────────────────────────────────────

def _apply_use_adjustment(
    classification: str,
    project_inputs: SetbackProjectInputs,
    baseline_source: str,
) -> tuple[float | None, str | None, AdjustmentStep | None, list[str]]:
    """Check for use-based yard adjustments (GF commercial exception).

    Returns:
        (use_adjusted_yard_ft, use_adjustment_reason, chain_step, review_reasons)
        use_adjusted_yard_ft = None when not applicable or amount not determined.
    """
    review: list[str] = []

    gf_commercial = project_inputs.ground_floor_commercial
    lowest_res = project_inputs.lowest_residential_story

    # Use adjustment for side/rear when GF commercial and residential starts above grade
    if gf_commercial and lowest_res is not None and lowest_res > 1:
        if classification in ("side", "side_street_side", "rear"):
            reason = (
                f"Ground-floor commercial present (lowest residential story: {lowest_res}). "
                "Commercial ground-floor side/rear yard may differ from the residential "
                f"formula above — not auto-calculated. Residential formula ({baseline_source}) "
                "applied to the residential portion; confirm commercial ground-floor "
                "yard requirement separately."
            )
            review.append(
                "Ground-floor commercial: confirm whether commercial ground-floor "
                "side/rear yard differs from the residential formula. Apply separately "
                "if required — do not rely solely on this calculation."
            )
            step = AdjustmentStep(
                adjustment="use adjustment: GF commercial — amount not calculated",
                reason=reason,
                section=baseline_source,
                value_ft=None,
            )
            return None, reason, step, review

    return None, None, None, []


def _apply_adjacency(
    edge_id: str,
    classification: str,
    project_inputs: SetbackProjectInputs,
    authority_result: SetbackAuthorityResult,
    baseline_source: str,
) -> tuple[float | None, str | None, str, bool, AdjustmentStep | None, list[str]]:
    """Evaluate adjacency zone for more-restrictive yard requirements.

    Returns:
        (adj_yard_ft, adj_reason, adj_status, adj_more_restrictive,
         chain_step, review_reasons)

        adj_yard_ft         — None if not applicable, not provided, or amount unknown
        adj_reason          — non-None when adjacency IS more restrictive (pending flag)
        adj_status          — "not_provided" / "checked" / "not_applicable"
        adj_more_restrictive — True when adj_zone detected as more restrictive
        chain_step          — AdjustmentStep to append to adjustment_chain
        review_reasons      — strings for manual_review_reasons
    """
    review: list[str] = []

    # Front edges: adjacency to residential does not apply in the same way
    if classification == "front":
        return None, None, "not_applicable", False, None, []

    adj_zone: str | None = project_inputs.per_edge_adjacency.get(edge_id)

    if adj_zone is None:
        return None, None, "not_provided", False, None, []

    # Explicit "none" or empty string: caller confirmed no adjacent zone concern
    if not adj_zone.strip() or adj_zone.strip().lower() in ("none", "n/a", "na"):
        step = AdjustmentStep(
            adjustment="adjacency: no restrictive adjacent zone",
            reason=f"Edge '{edge_id}': caller confirmed no adjacent zone concern.",
            section=baseline_source,
            value_ft=None,
        )
        return None, None, "checked", False, step, []

    if _is_more_restrictive_adjacency(adj_zone, authority_result.code_family):
        reason = (
            f"Adjacent zone '{adj_zone}' on edge '{edge_id}' is more restrictive "
            "than the project zone. Yard on this edge may need to increase to match "
            "the adjacent zone's yard requirement. "
            "Amount NOT auto-calculated — verify the applicable LAMC provision "
            f"for abutting more-restrictive-zone requirements "
            f"({baseline_source} or LAMC 12.08-A)."
        )
        review.append(
            f"Adjacency: edge '{edge_id}' abuts '{adj_zone}' (more restrictive). "
            "Manually verify required yard increase — governing provision and "
            "adjacent zone's yard minimum must be confirmed before using this result."
        )
        step = AdjustmentStep(
            adjustment=f"adjacency flag: '{adj_zone}' is more restrictive",
            reason=reason,
            section=f"{baseline_source} / LAMC 12.08-A (verify applicable provision)",
            value_ft=None,
        )
        return None, reason, "checked", True, step, review
    else:
        step = AdjustmentStep(
            adjustment=f"adjacency: '{adj_zone}' — no increase required",
            reason=(
                f"Adjacent zone '{adj_zone}' on edge '{edge_id}' is not more "
                "restrictive than the project zone. No adjacency yard increase applied."
            ),
            section=baseline_source,
            value_ft=None,
        )
        return None, None, "checked", False, step, []


def _apply_adu(
    edge_id: str,
    classification: str,
    project_inputs: SetbackProjectInputs,
) -> float | None:
    """Return the ADU setback override (4 ft) for qualifying side/rear edges.

    ONLY applies to the ADU building portion on edges listed in adu_edge_ids.
    Never applied to front or side_street_side.
    Primary building yard is governed by the baseline → adjusted chain;
    adu_override_yard_ft is a separate field in EdgeResult.
    """
    if not project_inputs.adu_present:
        return None
    if classification not in ("side", "rear"):
        return None
    if edge_id in project_inputs.adu_edge_ids:
        return _ADU_SETBACK_FT
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-edge calculators
# ─────────────────────────────────────────────────────────────────────────────

def _calc_front_edge(
    edge: ClassifiedEdge,
    formula: YardFormula,
    project_inputs: SetbackProjectInputs,
    yard_family_result: YardFamilyResult,
    issues: list[SetbackIssue],
) -> EdgeResult:
    """Front edge: no prevailing calculation. Always provisional.

    baseline_yard_ft=None (code minimum not hardcoded in formula library).
    governing_yard_ft=None. prevailing_setback_flag=True always.
    """
    manual_review: list[str] = [
        "Front yard: prevailing setback NOT calculated. "
        "The prevailing setback governs in practice (LAMC 12.08-C or zone-section "
        "equivalent) and must be determined through manual site analysis. "
        f"Code minimum: consult {formula.governing_section} — not hardcoded here."
    ]

    if edge.confidence == "manual_confirm":
        manual_review.append(
            f"Edge '{edge.edge_id}' is tentatively classified as 'front' — "
            "confirm this is the front lot line before applying front yard rules."
        )

    # RAS: ground-floor commercial front yard is a separate question
    if (yard_family_result.status == "split_condition"
            and yard_family_result.ras_ground_floor_formula is None):
        manual_review.append(
            "RAS zone: ground-floor commercial front yard not resolved — "
            "residential formula above and commercial ground floor must be "
            "confirmed separately."
        )

    return EdgeResult(
        edge_id=edge.edge_id,
        edge_classification="front",
        edge_classification_confidence=edge.confidence,
        baseline_yard_ft=None,
        baseline_source=formula.governing_section,
        use_adjusted_yard_ft=None,
        use_adjustment_reason=None,
        adjacency_adjusted_yard_ft=None,
        adjacency_adjustment_reason=None,
        adjacency_status="not_applicable",
        adu_override_yard_ft=None,      # ADU override does not apply to front
        governing_yard_ft=None,
        governing_source="",
        adjustment_chain=[],
        status="provisional",
        manual_review_reasons=manual_review,
        prevailing_setback_flag=True,
        issues=issues,
    )


def _calc_side_rear_edge(
    edge: ClassifiedEdge,
    edge_input: EdgeInput | None,
    formula: YardFormula,
    authority_result: SetbackAuthorityResult,
    yard_family_result: YardFamilyResult,
    project_inputs: SetbackProjectInputs,
    issues: list[SetbackIssue],
) -> EdgeResult:
    """Side, rear, and side_street_side edge calculation chain."""
    classification = edge.classification
    edge_type = edge_input.edge_type if edge_input else "interior"
    is_alley_edge = (edge_type == "alley")
    manual_review: list[str] = []
    adjustment_chain: list[AdjustmentStep] = []

    # Manual-confirm note
    if edge.confidence == "manual_confirm":
        manual_review.append(
            f"Edge '{edge.edge_id}' classification '{classification}' is tentative "
            "(confidence=manual_confirm) — confirm edge role before using this result "
            "in permit calculations."
        )

    # Side_street_side note: exterior side yard may differ from interior side yard
    if classification == "side_street_side":
        manual_review.append(
            f"Side_street_side (corner lot secondary frontage) on edge '{edge.edge_id}': "
            "standard side yard formula applied. Verify whether the applicable zone "
            "section specifies a different exterior side yard requirement."
        )

    # RAS ground-floor commercial note
    ras_split_active = (
        yard_family_result.status == "split_condition"
        and not yard_family_result.cm_options
        and yard_family_result.ras_ground_floor_formula is None
    )
    if ras_split_active:
        manual_review.append(
            f"RAS zone: residential-above yard formula applied to edge '{edge.edge_id}'. "
            "Ground-floor commercial yard for this edge is NOT resolved here — "
            "confirm and apply separately."
        )

    # ── Baseline evaluation ──────────────────────────────────────────────────
    if classification == "rear":
        baseline_ft, chain_steps, is_provisional = _evaluate_rear_formula(
            formula, is_alley_edge, project_inputs.number_of_stories
        )
    else:
        baseline_ft, chain_steps, is_provisional = _evaluate_side_formula(
            formula, project_inputs.lot_width, project_inputs.number_of_stories
        )
    adjustment_chain.extend(chain_steps)
    baseline_source = formula.governing_section

    # ── Use adjustment ───────────────────────────────────────────────────────
    use_ft, use_reason, use_step, use_reviews = _apply_use_adjustment(
        classification, project_inputs, baseline_source
    )
    if use_step:
        adjustment_chain.append(use_step)
    manual_review.extend(use_reviews)
    use_adj_pending = (use_step is not None and use_ft is None)

    # ── Adjacency adjustment ─────────────────────────────────────────────────
    adj_ft, adj_reason, adj_status, adj_more_restrictive, adj_step, adj_reviews = (
        _apply_adjacency(
            edge.edge_id, classification,
            project_inputs, authority_result, baseline_source,
        )
    )
    if adj_step:
        adjustment_chain.append(adj_step)
    manual_review.extend(adj_reviews)
    adjacency_is_pending = adj_more_restrictive and adj_ft is None

    # ── ADU override ─────────────────────────────────────────────────────────
    adu_ft = _apply_adu(edge.edge_id, classification, project_inputs)
    if adu_ft is not None:
        adjustment_chain.append(AdjustmentStep(
            adjustment=f"ADU setback override: {adu_ft:.0f} ft (ADU portion only)",
            reason=(
                f"ADU present on edge '{edge.edge_id}'. "
                f"4 ft setback applies to the ADU building portion per "
                f"{_ADU_SETBACK_SECTION}. Primary building yard is unchanged."
            ),
            section=_ADU_SETBACK_SECTION,
            value_ft=adu_ft,
        ))

    # ── Governing yard ───────────────────────────────────────────────────────
    # Governing = most restrictive (highest value) among known applicable values.
    # If any pending/unknown adjustment exists, governing cannot be confirmed.
    if baseline_ft is None or adjacency_is_pending:
        governing_ft: float | None = None
        governing_source = ""
    else:
        candidates: list[tuple[float, str]] = [(baseline_ft, baseline_source)]
        if use_ft is not None:
            candidates.append((use_ft, "use adjustment"))
        if adj_ft is not None:
            candidates.append((adj_ft, f"adjacency requirement ({adj_reason or ''})"))
        governing_ft, governing_source = max(candidates, key=lambda x: x[0])

    # ── Status ───────────────────────────────────────────────────────────────
    provisional_flags = [
        edge.confidence == "manual_confirm",
        yard_family_result.status in ("provisional", "split_condition"),  # includes RAS
        authority_result.confidence in ("provisional", "unresolved"),
        baseline_ft is None,
        is_provisional,             # formula evaluation had provisional elements
        adjacency_is_pending,       # adjacency increase required but amount unknown
        use_adj_pending,            # use adjustment applies but amount not determined
        governing_ft is None,
        ras_split_active,
    ]
    status = "provisional" if any(provisional_flags) else "confirmed"

    return EdgeResult(
        edge_id=edge.edge_id,
        edge_classification=classification,
        edge_classification_confidence=edge.confidence,
        baseline_yard_ft=baseline_ft,
        baseline_source=baseline_source,
        use_adjusted_yard_ft=use_ft,
        use_adjustment_reason=use_reason,
        adjacency_adjusted_yard_ft=adj_ft,
        adjacency_adjustment_reason=adj_reason,
        adjacency_status=adj_status,
        adu_override_yard_ft=adu_ft,
        governing_yard_ft=governing_ft,
        governing_source=governing_source,
        adjustment_chain=adjustment_chain,
        status=status,
        manual_review_reasons=manual_review,
        prevailing_setback_flag=False,
        issues=issues,
    )


def _calc_single_edge(
    edge: ClassifiedEdge,
    edge_input: EdgeInput | None,
    authority_result: SetbackAuthorityResult,
    yard_family_result: YardFamilyResult,
    project_inputs: SetbackProjectInputs,
) -> EdgeResult:
    """Compute the full yard chain for one classified edge.

    Dispatches to type-specific calculators after handling cross-cutting
    conditions (early exit, CM split, unresolved yard family, missing formula).
    """
    issues: list[SetbackIssue] = []
    manual_review: list[str] = []

    # ── Early exit pass-through ──────────────────────────────────────────────
    if authority_result.early_exit.triggered:
        return EdgeResult(
            edge_id=edge.edge_id,
            edge_classification=edge.classification,
            edge_classification_confidence=edge.confidence,
            status="unresolved",
            prevailing_setback_flag=(edge.classification == "front"),
            manual_review_reasons=[
                f"Setback authority early exit: {authority_result.early_exit.reason}"
            ],
            issues=issues,
        )

    # ── Unresolved yard family ───────────────────────────────────────────────
    if yard_family_result is None or yard_family_result.status == "unresolved":
        return EdgeResult(
            edge_id=edge.edge_id,
            edge_classification=edge.classification,
            edge_classification_confidence=edge.confidence,
            status="unresolved",
            prevailing_setback_flag=(edge.classification == "front"),
            manual_review_reasons=[
                "Yard family unresolved — cannot compute yard values. "
                "Resolve authority step and yard family before edge calculation."
            ],
            issues=issues,
        )

    # ── CM split: cannot select a single yard family ──────────────────────────
    # Both candidate options are available in yard_family_result.cm_options;
    # neither is auto-selected. Return split_condition per edge.
    if yard_family_result.cm_options:
        options_summary = " / ".join(
            f"{opt.inherited_family} ({opt.use_type_label})"
            for opt in yard_family_result.cm_options
        )
        return EdgeResult(
            edge_id=edge.edge_id,
            edge_classification=edge.classification,
            edge_classification_confidence=edge.confidence,
            baseline_yard_ft=None,
            baseline_source="LAMC 12.17.5",
            governing_yard_ft=None,
            governing_source="",
            status="split_condition",
            prevailing_setback_flag=(edge.classification == "front"),
            manual_review_reasons=[
                f"CM zone split unresolved — yard cannot be calculated. "
                f"Candidate paths: {options_summary}. "
                "Confirm use type (R3 vs. R4 yards) before running edge calculation."
            ],
            issues=issues,
        )

    # ── Get formula for this edge classification ──────────────────────────────
    formula = _get_formula(edge.classification, yard_family_result)

    if formula is None:
        return EdgeResult(
            edge_id=edge.edge_id,
            edge_classification=edge.classification,
            edge_classification_confidence=edge.confidence,
            status="unresolved",
            prevailing_setback_flag=(edge.classification == "front"),
            manual_review_reasons=[
                f"No formula available for classification '{edge.classification}'. "
                "Check yard_family_result — formula fields may not be populated "
                "for this yard type."
            ],
            issues=issues,
        )

    # ── Dispatch by classification ────────────────────────────────────────────
    if edge.classification == "front":
        return _calc_front_edge(edge, formula, project_inputs, yard_family_result, issues)

    return _calc_side_rear_edge(
        edge, edge_input, formula,
        authority_result, yard_family_result, project_inputs, issues,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def calculate_edge_yards(
    authority_result: SetbackAuthorityResult,
    yard_family_result: YardFamilyResult,
    classified_edges: list[ClassifiedEdge],
    project_inputs: SetbackProjectInputs,
) -> list[EdgeResult]:
    """Compute per-edge yard chains for all classified edges.

    Args:
        authority_result:    Output of resolve_setback_authority().
        yard_family_result:  Output of get_yard_family_rules().
        classified_edges:    Output of classify_edges().
        project_inputs:      Project-level inputs including lot_width,
                             number_of_stories, per_edge_adjacency, etc.

    Returns:
        List of EdgeResult in the same order as classified_edges.
        Each result preserves the full baseline → adjusted → governing chain.
        Results are useful even when provisional — they carry the partial
        calculation and explicit reasons for any unresolved elements.

    Does NOT:
        - calculate prevailing front yard
        - auto-select CM yard option
        - assume a favorable adjacency condition when adjacency is unknown
        - collapse chain into a single governing number when any step is pending
        - produce status="confirmed" unless all inputs are fully resolved
    """
    if not classified_edges:
        return []

    # Build edge_id → EdgeInput index from project_inputs for edge_type lookup
    edge_input_index: dict[str, EdgeInput] = {
        e.edge_id: e for e in project_inputs.edges
    }

    return [
        _calc_single_edge(
            edge=edge,
            edge_input=edge_input_index.get(edge.edge_id),
            authority_result=authority_result,
            yard_family_result=yard_family_result,
            project_inputs=project_inputs,
        )
        for edge in classified_edges
    ]
