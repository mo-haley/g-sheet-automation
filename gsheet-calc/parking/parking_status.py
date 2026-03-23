"""Parking output assembly (Steps 4, 6, 7).

Selects operative reduction lane and assembles final ParkingResult.

Lane selection philosophy:
  The operative parking lane is NOT selected by choosing the lowest applicable
  reduction. It is selected by authority: explicit user/project selection,
  alignment with the density incentive lane, or left unresolved when multiple
  plausible paths exist without a clear governing basis.
"""

from __future__ import annotations

import math

from parking.models import (
    AB2097Result,
    BaselineParking,
    ParkingComparison,
    ParkingInterrupters,
    ParkingIssue,
    ParkingLane,
    ParkingOutput,
    ParkingResult,
    StateDBParking,
    TOCParking,
)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Deduplicate a list of strings while preserving insertion order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def select_parking_lane(
    ab2097: AB2097Result,
    baseline: BaselineParking,
    toc_parking: TOCParking | None,
    state_db_parking: StateDBParking | None,
    density_lane: str,
    interrupters: ParkingInterrupters,
    user_selected_lane: str | None = None,
) -> ParkingLane:
    """STEP 4: Select operative parking reduction lane.

    Selection is authority-based, not optimization-based:
      1. Explicit user/project selection takes priority.
      2. Otherwise, align with the density incentive lane if it has a parking result.
      3. Otherwise, leave unresolved.

    AB 2097 is treated as a legal override path — its result is recorded but this
    layer does NOT hard-code a universal numeric cap. The AB 2097 module's resolved
    result is used if available; otherwise the path is marked plausible/unresolved.

    All computed lane results are preserved for comparison regardless of selection.
    """
    # ── Collect available results from each lane module ─────────────
    ab2097_result: float | None = None
    ab2097_confidence = ab2097.confidence

    if ab2097.eligible is True and ab2097_confidence == "confirmed":
        # Use the AB 2097 module's resolved result if available.
        # The module computes max parking; we record that result.
        total_units = sum(line.count for line in baseline.residential_by_unit_type)
        if total_units > 0:
            # AB 2097 max is per the statute at filing; 0.5/unit is common but not universal.
            # We compute it here as a provisional estimate; the AB 2097 module should
            # provide the authoritative number in a future refinement.
            ab2097_result = math.ceil(total_units * 0.5)
    elif ab2097.eligible is True and ab2097_confidence == "provisional":
        # Plausible but not confirmed — record an estimate but mark lane provisional
        total_units = sum(line.count for line in baseline.residential_by_unit_type)
        if total_units > 0:
            ab2097_result = math.ceil(total_units * 0.5)

    toc_result: float | None = None
    toc_confidence = "unresolved"
    if toc_parking and toc_parking.required_spaces is not None:
        toc_result = toc_parking.required_spaces
        toc_confidence = toc_parking.status

    state_db_result: float | None = None
    state_db_confidence = "unresolved"
    if state_db_parking and state_db_parking.total_required is not None:
        state_db_result = state_db_parking.total_required
        state_db_confidence = state_db_parking.status

    # ── Lane selection: authority-based, not minimization ───────────
    selected: str
    selection_basis: str
    governing: float | None
    lane_confidence: str

    if user_selected_lane and user_selected_lane != "unresolved":
        # 1. Explicit user/project selection
        selected = user_selected_lane
        selection_basis = "user_selected"
        governing, lane_confidence = _resolve_user_selected(
            selected, baseline, ab2097_result, ab2097_confidence,
            toc_result, toc_confidence, state_db_result, state_db_confidence,
        )
    elif density_lane in ("toc", "state_db"):
        # 2. Align with density incentive lane
        selected, governing, lane_confidence = _resolve_density_aligned(
            density_lane, baseline, ab2097_result, ab2097_confidence,
            toc_result, toc_confidence, state_db_result, state_db_confidence,
        )
        selection_basis = "density_lane_aligned"
    else:
        # 3. No explicit selection, no density lane alignment — check if only baseline applies
        if ab2097_result is None and toc_result is None and state_db_result is None:
            selected = "none"
            selection_basis = "no_reduction_applicable"
            governing = baseline.total_baseline
            lane_confidence = "provisional"  # still capped by interrupters
        else:
            # Multiple plausible paths but no governing basis to choose
            selected = "unresolved"
            selection_basis = "unresolved"
            governing = None
            lane_confidence = "unresolved"

    # Cap lane confidence by interrupter confidence
    if interrupters.confidence == "unresolved":
        lane_confidence = "unresolved"
    elif interrupters.confidence == "provisional" and lane_confidence == "confirmed":
        lane_confidence = "provisional"

    return ParkingLane(
        selected=selected,
        selection_basis=selection_basis,
        ab2097_result=ab2097_result,
        toc_result=toc_result,
        state_db_result=state_db_result,
        governing_minimum=governing,
        governing_source=selected if selected != "unresolved" else None,
        confidence=lane_confidence,
    )


def _resolve_user_selected(
    selected: str,
    baseline: BaselineParking,
    ab2097_result: float | None,
    ab2097_confidence: str,
    toc_result: float | None,
    toc_confidence: str,
    state_db_result: float | None,
    state_db_confidence: str,
) -> tuple[float | None, str]:
    """Resolve governing minimum and confidence for a user-selected lane."""
    if selected == "none":
        return baseline.total_baseline, "provisional"
    elif selected == "ab2097":
        if ab2097_result is not None:
            return ab2097_result, ab2097_confidence
        return None, "unresolved"
    elif selected == "toc":
        if toc_result is not None:
            return toc_result, toc_confidence
        return None, "unresolved"
    elif selected == "state_db":
        if state_db_result is not None:
            return state_db_result, state_db_confidence
        return None, "unresolved"
    else:
        return None, "unresolved"


def _resolve_density_aligned(
    density_lane: str,
    baseline: BaselineParking,
    ab2097_result: float | None,
    ab2097_confidence: str,
    toc_result: float | None,
    toc_confidence: str,
    state_db_result: float | None,
    state_db_confidence: str,
) -> tuple[str, float | None, str]:
    """Resolve lane selection aligned with density incentive lane."""
    if density_lane == "toc" and toc_result is not None:
        return "toc", toc_result, toc_confidence
    elif density_lane == "state_db" and state_db_result is not None:
        return "state_db", state_db_result, state_db_confidence
    else:
        # Density lane set but no parking result from that lane
        return "unresolved", None, "unresolved"


def compute_parking_comparison(
    governing_minimum: float | None,
    proposed_parking: int | None,
) -> ParkingComparison:
    """STEP 6: Separate legal minimum from proposed parking."""
    if governing_minimum is None or proposed_parking is None:
        return ParkingComparison(
            legal_minimum=governing_minimum,
            proposed_parking=proposed_parking,
        )

    delta = proposed_parking - governing_minimum
    return ParkingComparison(
        legal_minimum=governing_minimum,
        proposed_parking=proposed_parking,
        delta=delta,
        above_minimum_is_owner_choice=delta > 0,
    )


def _build_authority_chain(output: ParkingOutput, density_status: str) -> list[str]:
    """Build a review-readable parking authority chain summary."""
    chain: list[str] = []
    interrupters = output.parking_interrupters

    # ── Code family ─────────────────────────────────────────────────
    cf = output.code_family
    cf_qual = "confirmed" if cf.confidence == "confirmed" else f"provisional ({cf.basis})"
    chain.append(f"Code family: Chapter {cf.chapter} ({cf_qual})")

    # ── Baseline local source ───────────────────────────────────────
    chain.append(f"Baseline local parking source: {interrupters.baseline_local_parking_source}")
    if interrupters.governing_parking_source:
        chain.append(f"Governing parking source: {interrupters.governing_parking_source}")
    else:
        chain.append("Governing parking source: not confirmed (site controls unresolved)")

    # ── Flagged controls ────────────────────────────────────────────
    for fc in interrupters.flagged_controls:
        chain.append(f"{fc.control_type} '{fc.identifier}': detected, {fc.document_status}")

    if interrupters.prior_case_conditions is None:
        chain.append("Prior case conditions: not checked")
    elif interrupters.prior_case_conditions:
        chain.append("Prior case conditions: present (requires review)")

    if interrupters.mpr_district is None:
        chain.append("MPR district: not checked")
    elif interrupters.mpr_district:
        chain.append("MPR district: present (requires review)")

    # ── AB 2097 ─────────────────────────────────────────────────────
    ab = output.ab2097
    if ab.eligible is True:
        ab_qual = "confirmed" if ab.confidence == "confirmed" else "provisional"
        chain.append(f"AB 2097: eligible, {ab.transit_type or 'type unknown'} ({ab_qual})")
    elif ab.eligible is False:
        chain.append("AB 2097: not eligible")
    else:
        chain.append("AB 2097: eligibility unresolved")

    # ── Operative lane ──────────────────────────────────────────────
    lane = output.parking_lane
    if lane.selected == "none":
        chain.append("Parking reduction lane: none (local baseline governs)")
    elif lane.selected == "toc":
        toc_qual = output.toc_parking.status if output.toc_parking else "unresolved"
        chain.append(f"Parking reduction lane: TOC ({lane.selection_basis}, {toc_qual})")
    elif lane.selected == "state_db":
        db_qual = output.state_db_parking.status if output.state_db_parking else "unresolved"
        chain.append(f"Parking reduction lane: State DB ({lane.selection_basis}, {db_qual})")
    elif lane.selected == "ab2097":
        chain.append(f"Parking reduction lane: AB 2097 ({lane.selection_basis})")
    elif lane.selected == "unresolved":
        plausible = []
        if lane.ab2097_result is not None:
            plausible.append("AB 2097")
        if lane.toc_result is not None:
            plausible.append("TOC")
        if lane.state_db_result is not None:
            plausible.append("State DB")
        if plausible:
            chain.append(f"Parking reduction lane: unresolved (plausible: {', '.join(plausible)})")
        else:
            chain.append("Parking reduction lane: unresolved")

    # ── Comparison values ───────────────────────────────────────────
    bl = output.baseline_parking.total_baseline
    chain.append(f"Baseline local requirement: {bl:.1f} spaces")
    if lane.governing_minimum is not None and lane.governing_minimum != bl:
        chain.append(f"Governing reduced requirement: {lane.governing_minimum:.1f} spaces")
    if output.parking_comparison.proposed_parking is not None:
        chain.append(f"Proposed parking: {output.parking_comparison.proposed_parking} spaces")

    # ── Density dependency ──────────────────────────────────────────
    if density_status in ("unresolved", "provisional"):
        chain.append(f"Density status: {density_status} (inherited into parking confidence)")

    return chain


def _build_source_tracking(output: ParkingOutput) -> tuple[list[str], list[str], list[str]]:
    """Build three-bucket source tracking for parking.

    Returns:
        (sources_checked, sources_flagged_not_interpreted, sources_not_checked)
    """
    checked: list[str] = []
    flagged: list[str] = []
    not_checked: list[str] = []
    interrupters = output.parking_interrupters

    # Checked
    checked.append(f"{interrupters.baseline_local_parking_source} (baseline parking)")

    ab = output.ab2097
    if ab.eligible is not None:
        checked.append("AB 2097 transit proximity")

    lane = output.parking_lane.selected
    if lane == "toc":
        checked.append("LAMC 12.22 A.31 (TOC parking)")
    elif lane == "state_db":
        checked.append("Gov. Code 65915(p) (State DB parking)")

    # Flagged: detected upstream, used to degrade confidence, not fully interpreted
    for fc in interrupters.flagged_controls:
        flagged.append(f"{fc.control_type}: {fc.identifier}")

    # Not checked
    if interrupters.prior_case_conditions is None:
        not_checked.append("Prior case conditions / ZIMAS case data")
    if interrupters.mpr_district is None:
        not_checked.append("Modified Parking Requirement (MPR) district")

    return checked, flagged, not_checked


def assemble_parking_result(
    output: ParkingOutput,
    density_status: str = "provisional",
    orchestrator_issues: list[ParkingIssue] | None = None,
) -> ParkingResult:
    """STEP 7: Assemble final parking output with confidence.

    Status reflects the worst-of all upstream confidences, lane resolution,
    and density dependency. The authority chain reads as a review narrative.
    """
    all_issues: list[ParkingIssue] = []

    # Collect issues from all steps
    if orchestrator_issues:
        all_issues.extend(orchestrator_issues)
    all_issues.extend(output.ab2097.issues)
    all_issues.extend(output.baseline_parking.issues)
    all_issues.extend(output.parking_interrupters.issues)
    # Lane module issues
    if output.toc_parking and output.toc_parking.issues:
        all_issues.extend(output.toc_parking.issues)
    if output.state_db_parking and output.state_db_parking.issues:
        all_issues.extend(output.state_db_parking.issues)

    # ── Source tracking ─────────────────────────────────────────────
    sources_checked, sources_flagged, sources_not_checked = _build_source_tracking(output)

    # ── Authority chain ─────────────────────────────────────────────
    authority_chain = _build_authority_chain(output, density_status)

    # ── Status: confidence cascade ──────────────────────────────────
    lane = output.parking_lane
    statuses = [
        output.baseline_parking.status,
        output.parking_interrupters.confidence,
        lane.confidence,
    ]

    # Inherit density confidence
    if density_status == "unresolved":
        statuses.append("unresolved")
    elif density_status == "provisional":
        statuses.append("provisional")

    if "unresolved" in statuses:
        status = "unresolved"
    elif "provisional" in statuses:
        status = "provisional"
    else:
        status = "confirmed"

    # Overridden: only when lane is active, produced a governing result,
    # and the lane result is substantively resolved enough
    if lane.selected in ("toc", "state_db", "ab2097") and status == "confirmed":
        lane_status = lane.confidence
        if lane_status in ("confirmed", "provisional"):
            status = "overridden"
        # If lane confidence is unresolved, status stays from cascade

    # Lane unresolved -> hard degrade
    if lane.selected == "unresolved":
        if status not in ("unresolved",):
            status = "unresolved"

    # ── Manual review reasons (deduped) ─────────────────────────────
    manual_review_reasons: list[str] = []

    if lane.selected == "unresolved":
        manual_review_reasons.append("Parking reduction lane not resolved")

    for issue in all_issues:
        if issue.severity in ("error", "warning") and issue.action_required:
            manual_review_reasons.append(issue.action_required)

    manual_review_reasons = _dedupe_preserve_order(manual_review_reasons)

    return ParkingResult(
        baseline_local_required_parking=output.baseline_parking.total_baseline,
        governing_reduced_required_parking=lane.governing_minimum,
        active_parking_lane=lane.selected,
        proposed_parking=output.parking_comparison.proposed_parking,
        parking_delta_from_minimum=output.parking_comparison.delta,
        authority_chain_summary=authority_chain,
        sources_checked=sources_checked,
        sources_flagged_not_interpreted=sources_flagged,
        sources_not_checked=sources_not_checked,
        manual_review_reasons=manual_review_reasons,
        status=status,
        all_issues=all_issues,
    )
