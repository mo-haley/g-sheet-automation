"""Parking authority resolution: code family identification and authority interrupters.

Implements Parking Steps 1 and 3.

This file does NOT select the operative parking lane or compute parking numbers.
It resolves the authority chain and emits structured signals so downstream files
can make honest lane-selection and baseline decisions.
"""

from __future__ import annotations

from models.site import Site
from parking.models import (
    CodeFamily,
    FlaggedControl,
    LaneGatingSignals,
    ParkingInterrupters,
    ParkingIssue,
)


def identify_code_family(site: Site) -> CodeFamily:
    """STEP 1: Identify whether Chapter 1 or Chapter 1A governs parking.

    Default to Chapter 1 unless project filing date AND zone confirm Ch 1A.
    """
    chapter = site.zone_code_chapter or "unknown"

    if chapter == "chapter_1":
        return CodeFamily(chapter="1", basis="default_ch1", confidence="confirmed")
    elif chapter == "chapter_1a":
        return CodeFamily(chapter="1A", basis="confirmed_filing_date", confidence="confirmed")
    else:
        # Default to Ch 1 per spec, but mark provisional since it's heuristic
        return CodeFamily(chapter="1", basis="default_ch1", confidence="provisional")


def check_parking_interrupters(site: Site, code_family: CodeFamily) -> ParkingInterrupters:
    """STEP 3: Check whether site-specific controls override base parking standard.

    Emits:
      - confidence for the parking authority chain
      - flagged controls (detected but not interpreted)
      - lane gating signals for downstream
      - honest unknown states for unchecked items
    """
    issues: list[ParkingIssue] = []
    flagged_controls: list[FlaggedControl] = []
    confidence = "confirmed"
    has_unresolved_parking_interrupter = False
    unresolved_lane_blockers: list[str] = []

    sp_overrides: bool | None = None
    overlay_overrides: bool | None = None
    d_q_affects: bool | None = None

    # ── 1. Specific Plan ────────────────────────────────────────────
    if site.specific_plan:
        sp_overrides = None  # Unknown until document pulled
        has_unresolved_parking_interrupter = True
        flagged_controls.append(FlaggedControl(
            control_type="specific_plan",
            identifier=site.specific_plan,
            may_affect_parking=None,
            document_status="not_reviewed",
        ))
        unresolved_lane_blockers.append(f"Specific plan '{site.specific_plan}' not reviewed for parking")
        issues.append(ParkingIssue(
            step="STEP_3_parking_interrupters",
            field="specific_plan_parking",
            severity="warning",
            message=f"Specific plan '{site.specific_plan}' found but document not pulled. Cannot determine if it overrides parking.",
            action_required=f"Review specific plan '{site.specific_plan}' for parking provisions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # ── 2. CPIO / overlay ───────────────────────────────────────────
    cpio_overlays = [o for o in site.overlay_zones if "CPIO" in o.upper()]
    if cpio_overlays:
        overlay_overrides = None
        has_unresolved_parking_interrupter = True
        for cpio in cpio_overlays:
            flagged_controls.append(FlaggedControl(
                control_type="cpio",
                identifier=cpio,
                may_affect_parking=None,
                document_status="not_reviewed",
            ))
        unresolved_lane_blockers.append(f"CPIO overlay(s) {cpio_overlays} not reviewed for parking")
        issues.append(ParkingIssue(
            step="STEP_3_parking_interrupters",
            field="overlay_parking",
            severity="warning",
            message=f"CPIO overlay(s) {cpio_overlays} found but not parsed for parking provisions.",
            action_required="Review CPIO overlay(s) for parking modifications.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # ── 3. D limitation / Q conditions ──────────────────────────────
    if site.d_limitations or site.q_conditions:
        d_q_affects = None
        has_unresolved_parking_interrupter = True
        detail_items = []
        if site.d_limitations:
            for d in site.d_limitations:
                flagged_controls.append(FlaggedControl(
                    control_type="d_limitation",
                    identifier=d,
                    may_affect_parking=None,
                    document_status="not_reviewed",
                ))
                detail_items.append(f"D: {d}")
        if site.q_conditions:
            for q in site.q_conditions:
                flagged_controls.append(FlaggedControl(
                    control_type="q_condition",
                    identifier=q,
                    may_affect_parking=None,
                    document_status="not_reviewed",
                ))
                detail_items.append(f"Q: {q}")
        unresolved_lane_blockers.append(f"D/Q conditions ({', '.join(detail_items)}) not reviewed for parking")
        issues.append(ParkingIssue(
            step="STEP_3_parking_interrupters",
            field="d_q_parking",
            severity="warning",
            message=f"D/Q conditions present ({', '.join(detail_items)}) but not reviewed for parking impact.",
            action_required="Review D/Q ordinances for parking requirements or restrictions.",
            confidence_impact="degrades_to_provisional",
        ))
        confidence = "provisional"

    # ── 4. Prior case conditions ────────────────────────────────────
    # Not checked — honest unknown
    prior_case: bool | None = None
    issues.append(ParkingIssue(
        step="STEP_3_parking_interrupters",
        field="prior_case_conditions",
        severity="info",
        message="Prior case conditions / entitlements not checked for parking impact. ZIMAS case data not queried.",
        action_required="Check ZIMAS for case numbers or prior entitlements affecting parking.",
        confidence_impact="degrades_to_provisional",
    ))
    if confidence == "confirmed":
        confidence = "provisional"

    # ── 5. MPR district ─────────────────────────────────────────────
    # Not checked — honest unknown
    mpr: bool | None = None
    issues.append(ParkingIssue(
        step="STEP_3_parking_interrupters",
        field="mpr_district",
        severity="info",
        message="Modified Parking Requirement (MPR) district status not checked.",
        action_required="Verify whether parcel is within an MPR district.",
        confidence_impact="degrades_to_provisional",
    ))

    # ── Baseline vs governing source ────────────────────────────────
    # Baseline local source is always LAMC 12.21 A.4 (the statutory default).
    # Governing source is ONLY populated when no unresolved interrupter could
    # override the parking standard. If any interrupter is unresolved, governing = None.
    if has_unresolved_parking_interrupter:
        governing_source = None
    else:
        governing_source = "LAMC 12.21 A.4"

    # ── Lane gating signals ─────────────────────────────────────────
    lane_gating = _build_lane_gating(site, code_family, unresolved_lane_blockers)

    return ParkingInterrupters(
        specific_plan_overrides_parking=sp_overrides,
        overlay_overrides_parking=overlay_overrides,
        d_q_affects_parking=d_q_affects,
        prior_case_conditions=prior_case,
        mpr_district=mpr,
        flagged_controls=flagged_controls,
        baseline_local_parking_source="LAMC 12.21 A.4",
        governing_parking_source=governing_source,
        lane_gating=lane_gating,
        confidence=confidence,
        issues=issues,
    )


def _build_lane_gating(
    site: Site,
    code_family: CodeFamily,
    unresolved_blockers: list[str],
) -> LaneGatingSignals:
    """Build lane-gating signals for downstream lane-selection logic.

    These signals indicate plausibility, not selection. Downstream files
    decide the operative lane; this just helps them avoid guessing.
    """
    code_family_resolved = code_family.confidence == "confirmed"

    # AB 2097: plausible if site has transit proximity data suggesting eligibility
    ab2097_plausible: bool | None = None
    if site.ab2097_area is True:
        ab2097_plausible = True
    elif site.ab2097_area is False:
        ab2097_plausible = False
    elif site.nearest_transit_stop_distance_ft is not None:
        ab2097_plausible = site.nearest_transit_stop_distance_ft <= 2640.0  # 1/2 mile in feet
    # else: None (insufficient data)

    # TOC: plausible if site has a TOC tier from ZIMAS
    toc_plausible: bool | None = None
    if site.toc_tier is not None and site.toc_tier > 0:
        toc_plausible = True
    elif site.toc_tier == 0:
        toc_plausible = False
    # else: None (no data)

    # State DB: always plausible in principle for any residential project;
    # actual eligibility depends on affordability (not checked here)
    state_db_plausible: bool | None = True

    return LaneGatingSignals(
        code_family_resolved=code_family_resolved,
        ab2097_plausible=ab2097_plausible,
        toc_plausible=toc_plausible,
        state_db_plausible=state_db_plausible,
        unresolved_controls_that_may_affect_lanes=unresolved_blockers,
    )
