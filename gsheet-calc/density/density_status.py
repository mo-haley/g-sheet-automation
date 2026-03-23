"""Density output assembly (Steps 6 and 7).

Runs eligibility/replacement checks and assembles the final DensityResult.
"""

from __future__ import annotations

from density.models import (
    AuthorityInterrupters,
    BaselineDensity,
    DensityIssue,
    DensityOutput,
    DensityResult,
    EligibilityChecks,
    IncentiveLane,
    ParcelRegime,
    StateDBDensity,
    TOCDensity,
)
from models.project import Project


def run_eligibility_checks(
    project: Project,
    incentive_lane: IncentiveLane,
    baseline: BaselineDensity,
) -> EligibilityChecks:
    """STEP 6: Run replacement / eligibility / qualification checks."""
    issues: list[DensityIssue] = []

    # RSO / Ellis Act replacement
    rso_required = None
    rso_units = None
    if project.existing_units_removed and project.existing_units_removed > 0:
        rso_required = True
        rso_units = project.existing_units_removed
        issues.append(DensityIssue(
            step="STEP_6_eligibility",
            field="rso_replacement",
            severity="warning",
            message=f"Site had {project.existing_units_removed} existing units removed. RSO/Ellis Act replacement obligations may apply.",
            action_required="Confirm RSO status and replacement unit count with HCIDLA.",
            confidence_impact="degrades_to_provisional",
        ))
    elif project.replacement_units_required:
        rso_required = True
        issues.append(DensityIssue(
            step="STEP_6_eligibility",
            field="rso_replacement",
            severity="warning",
            message="Replacement units flagged as required. Confirm count.",
            action_required="Confirm replacement unit count with HCIDLA.",
            confidence_impact="degrades_to_provisional",
        ))

    # SB 330 / Housing Crisis Act
    sb330 = None
    if project.existing_units_removed and project.existing_units_removed > 0:
        sb330 = True
        issues.append(DensityIssue(
            step="STEP_6_eligibility",
            field="sb330",
            severity="info",
            message="SB 330 may apply: site previously had residential units. Density reduction may be prohibited.",
            action_required="Verify SB 330 applicability.",
            confidence_impact="none",
        ))

    # TOC affordability check
    toc_aff_met = None
    if incentive_lane.selected == "toc" and project.affordability:
        toc_aff_met = None
        issues.append(DensityIssue(
            step="STEP_6_eligibility",
            field="toc_affordability",
            severity="warning",
            message="TOC affordability set-aside requirements not verified against operative TOC guidelines for the selected tier.",
            action_required="Verify affordability meets TOC tier requirements.",
            confidence_impact="degrades_to_provisional",
        ))

    # State DB affordability check
    state_db_aff_met = None
    if incentive_lane.selected == "state_db" and project.affordability:
        total_aff = (
            project.affordability.eli_pct + project.affordability.vli_pct +
            project.affordability.li_pct + project.affordability.moderate_pct
        )
        state_db_aff_met = total_aff > 0
        if not state_db_aff_met:
            issues.append(DensityIssue(
                step="STEP_6_eligibility",
                field="state_db_affordability",
                severity="warning",
                message="No affordable set-aside detected. State DB requires minimum affordability.",
                action_required="Provide affordability set-aside to qualify for State DB.",
                confidence_impact="degrades_to_provisional",
            ))

    # No net loss
    no_net_loss = None
    if project.existing_units_removed and project.existing_units_removed > 0:
        no_net_loss = True

    return EligibilityChecks(
        rso_replacement_required=rso_required,
        rso_replacement_units=rso_units,
        sb330_applies=sb330,
        toc_affordability_met=toc_aff_met,
        state_db_affordability_met=state_db_aff_met,
        no_net_loss_flag=no_net_loss,
        issues=issues,
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


def _has_material_eligibility_uncertainty(output: DensityOutput) -> bool:
    """Check if eligibility issues are material enough to cap status at provisional."""
    checks = output.eligibility_checks
    lane = output.incentive_lane.selected

    # Unresolved RSO/replacement where units were removed
    if checks.rso_replacement_required is True and checks.rso_replacement_units is not None:
        return True

    # TOC affordability not verified when TOC lane is active
    if lane == "toc" and checks.toc_affordability_met is None:
        return True

    # State DB lane active and 100% affordable not explicitly confirmed
    if lane == "state_db" and output.state_db_density:
        if (output.state_db_density.is_100_pct_affordable
                and not output.state_db_density.is_100_pct_affordable_confirmed):
            return True

    return False


def _build_authority_chain(output: DensityOutput) -> list[str]:
    """Build a review-readable authority chain summary.

    Includes trust cues for provisional/unresolved cases, not just final numbers.
    """
    chain: list[str] = []
    baseline = output.baseline_density
    regime = output.parcel_regime
    lane = output.incentive_lane.selected

    # ── Density factor provenance ───────────────────────────────────
    if baseline.sf_per_du_source:
        if baseline.used_governing_density:
            chain.append(f"Density factor: {baseline.sf_per_du_used} sf/du from {baseline.sf_per_du_source} (governing, authority-cleared)")
        else:
            chain.append(f"Density factor: {baseline.sf_per_du_used} sf/du from {baseline.sf_per_du_source} (zone-derived baseline; governing not confirmed)")

    # ── Lot area provenance ─────────────────────────────────────────
    if baseline.lot_area_used is not None:
        lot_qual = ""
        if baseline.lot_area_basis_confidence == "confirmed":
            lot_qual = "survey-confirmed"
        elif baseline.lot_area_basis_confidence == "provisional":
            lot_qual = "provisional — survey missing or area mismatch"
        else:
            lot_qual = "unresolved"
        chain.append(f"Lot area: {baseline.lot_area_used:,.0f} sf ({baseline.lot_area_source}, {lot_qual})")

    # ── Baseline units ──────────────────────────────────────────────
    if baseline.baseline_units is not None:
        chain.append(f"Baseline units: {baseline.baseline_units} (before incentives)")

    # ── Authority interrupters that degrade confidence ──────────────
    if regime.specific_plan:
        chain.append(f"Specific plan '{regime.specific_plan}': flagged, not fully interpreted for density")
    if regime.cpio:
        sub = f" subarea {regime.cpio_subarea}" if regime.cpio_subarea else ""
        chain.append(f"CPIO '{regime.cpio}'{sub}: flagged, not fully interpreted for density")
    if regime.d_limitation:
        chain.append(f"D limitation '{regime.d_limitation}': flagged, not reviewed for density impact")
    if regime.q_condition:
        chain.append(f"Q condition '{regime.q_condition}': flagged, not reviewed for density impact")

    interrupters = output.authority_interrupters
    if interrupters.prior_entitlements_present is None:
        chain.append("Prior entitlements: not checked")
    elif interrupters.prior_entitlements_present:
        chain.append("Prior entitlements: present (requires review)")

    # ── Incentive lane ──────────────────────────────────────────────
    if lane == "toc" and output.toc_density and output.toc_density.total_units is not None:
        tier_qual = "verified" if output.toc_density.tier_verified else "ZIMAS-only, not independently verified"
        chain.append(
            f"TOC Tier {output.toc_density.tier} ({tier_qual}): "
            f"+{output.toc_density.percentage_increase:.0%} = {output.toc_density.total_units} total units"
        )
    elif lane == "state_db" and output.state_db_density:
        db = output.state_db_density
        if db.bonus_percentage_is_unlimited:
            if db.is_100_pct_affordable_confirmed:
                chain.append(f"State DB: unlimited density confirmed ({db.statutory_authority})")
            else:
                chain.append(f"State DB: 100% affordable path claimed, unlimited density not yet confirmed ({db.statutory_authority})")
            # Surface comparison completeness
            if not db.governing_base_confirmed:
                chain.append(f"  DB base density comparison incomplete: evaluated {db.comparison_legs_evaluated}, unresolved {db.comparison_legs_unresolved}")
        elif db.total_units is not None:
            chain.append(f"State DB: +{db.bonus_percentage:.0f}% = {db.total_units} total units ({db.statutory_authority})")
            if not db.governing_base_confirmed:
                chain.append(f"  DB base density comparison incomplete: evaluated {db.comparison_legs_evaluated}, unresolved {db.comparison_legs_unresolved}")
    elif lane == "none":
        chain.append("Incentive lane: none")
    elif lane == "unresolved":
        chain.append("Incentive lane: not selected")

    return chain


def _build_source_tracking(output: DensityOutput) -> tuple[list[str], list[str], list[str]]:
    """Build three-bucket source tracking.

    Returns:
        (sources_checked, sources_flagged_not_interpreted, sources_not_checked)
    """
    checked: list[str] = []
    flagged: list[str] = []
    not_checked: list[str] = []

    baseline = output.baseline_density
    regime = output.parcel_regime
    interrupters = output.authority_interrupters

    # Checked sources
    if baseline.sf_per_du_source:
        checked.append(baseline.sf_per_du_source)
    if baseline.lot_area_source:
        checked.append(f"Lot area ({baseline.lot_area_source})")

    # Flagged but not fully interpreted: detected upstream, used to degrade confidence,
    # but the actual document content has not been parsed for density provisions
    if regime.specific_plan:
        flagged.append(f"Specific plan: {regime.specific_plan}")
    if regime.cpio:
        flagged.append(f"CPIO: {regime.cpio}")
    if regime.d_limitation:
        flagged.append(f"D limitation: {regime.d_limitation}")
    if regime.q_condition:
        flagged.append(f"Q condition: {regime.q_condition}")

    # GP lookup: checked if resolved, flagged if not
    gp_lookup = interrupters.gp_density_lookup
    if gp_lookup and gp_lookup.gp_density_resolved:
        checked.append(f"GP density scaffold ({gp_lookup.designation})")
    elif gp_lookup and gp_lookup.designation:
        flagged.append(f"GP land use '{gp_lookup.designation}': density range not resolved")

    # Not checked at all
    if interrupters.prior_entitlements_present is None:
        not_checked.append("Prior entitlements / ZIMAS case data")

    return checked, flagged, not_checked


def assemble_density_result(output: DensityOutput) -> DensityResult:
    """STEP 7: Assemble final density output with confidence.

    Status reflects the worst-of all upstream confidences, eligibility
    uncertainty, and incentive lane resolution. The authority chain
    summary reads as a review narrative, not just a final answer.
    """
    all_issues: list[DensityIssue] = []

    # Collect issues from all steps
    all_issues.extend(output.authority_interrupters.issues)
    all_issues.extend(output.baseline_density.issues)
    all_issues.extend(output.eligibility_checks.issues)
    if output.toc_density:
        all_issues.extend(output.toc_density.issues)
    if output.state_db_density:
        all_issues.extend(output.state_db_density.issues)

    # ── Source tracking ─────────────────────────────────────────────
    sources_checked, sources_flagged, sources_not_checked = _build_source_tracking(output)

    # ── Authority chain ─────────────────────────────────────────────
    authority_chain = _build_authority_chain(output)

    # ── Claimed density and active lane ─────────────────────────────
    baseline_units = output.baseline_density.baseline_units
    lane = output.incentive_lane.selected
    claimed_units: int | None = baseline_units
    is_unlimited = False

    if lane == "toc" and output.toc_density and output.toc_density.total_units is not None:
        claimed_units = output.toc_density.total_units
    elif lane == "state_db" and output.state_db_density:
        if output.state_db_density.bonus_percentage_is_unlimited:
            claimed_units = None
            is_unlimited = True
        elif output.state_db_density.total_units is not None:
            claimed_units = output.state_db_density.total_units

    # ── Status: confidence cascade ──────────────────────────────────
    statuses = [output.baseline_density.status, output.authority_interrupters.confidence]
    if output.toc_density:
        statuses.append(output.toc_density.status)
    if output.state_db_density:
        statuses.append(output.state_db_density.status)

    if "unresolved" in statuses:
        status = "unresolved"
    elif "provisional" in statuses:
        status = "provisional"
    else:
        status = "confirmed"

    # Incentive lane overridden: only when lane is active, produced a result,
    # and the lane result is at least provisional (not unresolved)
    if lane in ("toc", "state_db") and status == "confirmed":
        lane_status = None
        if lane == "toc" and output.toc_density:
            lane_status = output.toc_density.status
        elif lane == "state_db" and output.state_db_density:
            lane_status = output.state_db_density.status

        if lane_status in ("confirmed", "provisional"):
            # Lane actually produced a governing result — upgrade to overridden
            # only if cascade was clean enough (status was "confirmed" here)
            status = "overridden"
        # If lane_status is unresolved, status stays as-is from cascade

    # Lane unresolved -> hard degrade
    if lane == "unresolved":
        status = "unresolved"

    # Material eligibility uncertainty caps at provisional
    if status in ("confirmed", "overridden") and _has_material_eligibility_uncertainty(output):
        status = "provisional"

    # ── Manual review reasons (deduped) ─────────────────────────────
    manual_review_reasons: list[str] = []

    if lane == "unresolved":
        manual_review_reasons.append("Incentive lane not selected")

    for issue in all_issues:
        if issue.severity in ("error", "warning") and issue.action_required:
            manual_review_reasons.append(issue.action_required)

    manual_review_reasons = _dedupe_preserve_order(manual_review_reasons)

    return DensityResult(
        baseline_units_before_incentives=baseline_units,
        claimed_density_units=claimed_units,
        claimed_density_is_unlimited=is_unlimited,
        active_density_lane=lane,
        ed1_pathway=output.incentive_lane.ed1_pathway,
        authority_chain_summary=authority_chain,
        sources_checked=sources_checked,
        sources_flagged_not_interpreted=sources_flagged,
        sources_not_checked=sources_not_checked,
        manual_review_reasons=manual_review_reasons,
        status=status,
        all_issues=all_issues,
    )
