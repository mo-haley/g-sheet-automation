"""Density module orchestrator.

Thin orchestrator that calls each density step in sequence.

Pipeline:
    establish_parcel_regime      → parcel_regime
    map_zone_to_density_standard → density_standard
    check_authority_interrupters → authority_interrupters
    compute_baseline_density     → baseline_density
    IncentiveLane record         → incentive_lane
    compute_toc_density          → toc_density (if lane=toc)
    compute_state_db_density     → state_db_density (if lane=state_db)
    run_eligibility_checks       → eligibility_checks
    assemble_density_result      → density_result

Output: DensityOutput

Usage:
    from density.density_orchestrator import run_density, run_density_module
    from density.density_orchestrator import derive_development_posture

    # Comparison mode (default — posture-filtered route comparison):
    result = run_density_module(site, project)

    # Decision-grade mode (selected lane required for G-sheet output):
    result = run_density_module(site, project, selected_lane="toc")

    # With explicit posture override:
    result = run_density_module(site, project, development_posture="affordable_100")
"""

from __future__ import annotations

from density.density_authority import (
    check_authority_interrupters,
    establish_parcel_regime,
    map_zone_to_density_standard,
)
from density.density_baseline_calc import compute_baseline_density
from density.density_state_db_calc import compute_state_db_density
from density.density_status import assemble_density_result, run_eligibility_checks
from density.density_toc_calc import compute_toc_density
from density.models import DensityOutput, IncentiveLane, ParcelRegime
from models.project import Project
from models.site import Site

from models.result_common import (
    ActionPosture,
    CoverageLevel,
    ConfidenceLevel,
    Interpretation,
    ModuleResult,
    Provenance,
    RunStatus,
)


def run_density(
    site: Site,
    project: Project,
    incentive_lane: str = "unresolved",
    ed1_pathway: bool = False,
    lane_selected_by: str = "unresolved",
) -> DensityOutput:
    """Execute the full density decision sequence.

    Args:
        site: Parsed site data.
        project: Project assumptions.
        incentive_lane: One of "none", "toc", "state_db", "unresolved".
        ed1_pathway: Whether ED1 processing pathway is claimed.
        lane_selected_by: "user" or "auto" or "unresolved".
    """
    output = DensityOutput()

    # STEP 1 - Establish parcel regime
    output.parcel_regime = establish_parcel_regime(site)

    # STEP 1.5 - Map zone to density standard
    output.density_standard, std_issues = map_zone_to_density_standard(output.parcel_regime)

    # STEP 2 - Check authority interrupters
    output.authority_interrupters = check_authority_interrupters(
        output.parcel_regime, output.density_standard
    )

    # STEP 3 - Compute baseline density
    output.baseline_density = compute_baseline_density(
        site, output.parcel_regime, output.authority_interrupters
    )

    # STEP 4 - Record incentive lane selection
    output.incentive_lane = IncentiveLane(
        selected=incentive_lane,
        ed1_pathway=ed1_pathway,
        selected_by=lane_selected_by,
        confidence="confirmed" if incentive_lane in ("none", "toc", "state_db") and lane_selected_by == "user" else "unresolved" if incentive_lane == "unresolved" else "provisional",
    )

    # STEP 5a - TOC density (if lane = toc)
    if incentive_lane == "toc":
        output.toc_density = compute_toc_density(
            output.parcel_regime, output.baseline_density
        )

    # STEP 5b - State DB density (if lane = state_db)
    if incentive_lane == "state_db":
        output.state_db_density = compute_state_db_density(
            site, output.parcel_regime, output.authority_interrupters,
            output.baseline_density, project,
        )

    # STEP 6 - Eligibility checks
    output.eligibility_checks = run_eligibility_checks(
        project, output.incentive_lane, output.baseline_density
    )

    # STEP 7 - Assemble result
    output.density_result = assemble_density_result(output)

    return output


# ── Development posture ───────────────────────────────────────────────────────


def derive_development_posture(project: Project, override: str | None = None) -> str:
    """Derive the development posture from project affordability fields.

    Returns one of: "market_rate", "mixed", "affordable_100", "unknown".

    Derivation order:
        1. Explicit override — caller knows best.
        2. Affordability plan fields — most precise signal.
        3. Default: "market_rate".

    "unknown" signals that no posture filter should be applied to the route set.
    It is returned when an unrecognized override string is passed.
    """
    if override is not None:
        if override in ("market_rate", "mixed", "affordable_100", "unknown"):
            return override
        return "unknown"

    # Explicit checkbox takes precedence over percentage derivation.
    if project.hundred_pct_affordable is True:
        return "affordable_100"
    if project.hundred_pct_affordable is False:
        # Explicitly not 100% affordable — derive mixed vs market from percentages.
        aff = project.affordability
        if aff is None:
            return "market_rate"
        total_affordable = aff.eli_pct + aff.vli_pct + aff.li_pct + aff.moderate_pct
        if total_affordable > 0.0:
            return "mixed"
        return "market_rate"

    # hundred_pct_affordable is None — derive from affordability percentages as before.
    aff = project.affordability
    if aff is None:
        return "market_rate"

    total_affordable = aff.eli_pct + aff.vli_pct + aff.li_pct + aff.moderate_pct
    if aff.market_pct == 0.0 and total_affordable >= 99.0:
        return "affordable_100"
    if total_affordable > 0.0:
        return "mixed"

    return "market_rate"


# ── Candidate lane filtering ──────────────────────────────────────────────────


def _candidate_lanes_and_excluded(
    posture: str,
    regime: ParcelRegime,
) -> tuple[list[str], list[dict]]:
    """Return (lanes_to_run, excluded_routes) for the given posture and regime.

    "none" (baseline) is always included and always first.
    Additional lanes are posture-relevant, not exhaustively possible.
    Routes that are filtered out are preserved in excluded_routes with reasons
    so the caller can surface them if needed.
    """
    included: list[str] = ["none"]
    excluded: list[dict] = []

    # TOC: include only when the site appears in a TOC zone.
    toc_eligible = regime.toc_tier_zimas is not None and regime.toc_tier_zimas >= 1
    if toc_eligible:
        included.append("toc")
    else:
        excluded.append({
            "lane": "toc",
            "reason": "site does not appear in a TOC zone (toc_tier not set in parcel data)",
        })

    # State DB: posture-gated. Market-rate projects haven't signaled an affordability
    # commitment, so showing State DB routes would imply a design decision they haven't made.
    if posture == "market_rate":
        excluded.append({
            "lane": "state_db",
            "reason": (
                "requires affordability commitment not indicated by project posture (market_rate); "
                "add an affordability plan to enable State DB comparison"
            ),
        })
    else:
        # mixed, affordable_100, unknown all get State DB
        included.append("state_db")

    return included, excluded


# ── Per-lane route runner ─────────────────────────────────────────────────────


def _run_lane(
    site: Site,
    project: Project,
    lane: str,
    ed1_pathway: bool = False,
) -> dict:
    """Run the density engine for one candidate lane and return a route summary dict.

    The full DensityOutput is not retained — only route-level facts are extracted.
    The primary (baseline) output is handled separately in run_density_module.
    """
    output = run_density(
        site, project,
        incentive_lane=lane,
        ed1_pathway=ed1_pathway,
        lane_selected_by="auto",
    )

    dr = output.density_result

    # Extract the lane-specific unit count.
    if lane == "toc" and output.toc_density is not None:
        units = output.toc_density.total_units
        unlimited = False
    elif lane == "state_db" and output.state_db_density is not None:
        unlimited = output.state_db_density.bonus_percentage_is_unlimited
        units = None if unlimited else output.state_db_density.total_units
    else:
        units = output.baseline_density.baseline_units
        unlimited = False

    if dr.status in ("confirmed", "overridden") and (units is not None or unlimited):
        route_status = "computed"
    elif dr.status == "unresolved" or (units is None and not unlimited):
        route_status = "requires_more_info"
    else:
        route_status = "computed"

    if dr.status in ("confirmed", "overridden"):
        confidence = "high"
    elif dr.status == "provisional":
        confidence = "provisional"
    else:
        confidence = "unresolved"

    # Dedupe and cap gap reasons for readability.
    gap_reasons = list(dict.fromkeys(r for r in dr.manual_review_reasons if r))[:3]

    return {
        "lane": lane,
        "units": units,
        "unlimited": unlimited,
        "status": route_status,
        "confidence": confidence,
        "gap_reasons": gap_reasons,
    }


def _build_baseline_route_dict(output: DensityOutput) -> dict:
    """Build a candidate route dict for the baseline (lane=none) from the primary output."""
    bd = output.baseline_density
    return {
        "lane": "none",
        "units": bd.baseline_units,
        "unlimited": False,
        "status": "computed" if bd.baseline_units is not None else "requires_more_info",
        "confidence": bd.status if bd.status in ("confirmed", "provisional") else "unresolved",
        "gap_reasons": [i.action_required for i in bd.issues if i.severity == "error"][:3],
    }


def _best_candidate_route(candidate_routes: list[dict]) -> dict | None:
    """Pick the strongest non-baseline candidate route for the summary sentence.

    Prefers unlimited routes (100% affordable path), then highest unit count,
    then first requires_more_info route.
    """
    alternatives = [r for r in candidate_routes if r["lane"] != "none"]
    if not alternatives:
        return None
    unlimited = [r for r in alternatives if r.get("unlimited")]
    if unlimited:
        return unlimited[0]
    with_units = [r for r in alternatives if r["units"] is not None and r["status"] == "computed"]
    if with_units:
        return max(with_units, key=lambda r: r["units"])
    return alternatives[0]


# ── ModuleResult adapter helpers ──────────────────────────────────────────────


def _map_coverage_level(output: DensityOutput) -> CoverageLevel:
    """Derive coverage from what data is available.

    UNCERTAIN: no usable density factor (zone lookup failed or CM zone).
    THIN:      lot area missing but density factor known.
    PARTIAL:   factor and lot area present; authority interrupters unresolved.
    COMPLETE:  factor and lot area present; no SP/CPIO/D/Q interrupters.

    Coverage reflects input completeness, not confidence in the answer.
    Prior entitlements (always unchecked) do not degrade coverage here —
    they degrade confidence via density_result.status instead.
    """
    ds = output.density_standard
    bd = output.baseline_density
    regime = output.parcel_regime

    if ds.sf_per_du is None:
        return CoverageLevel.UNCERTAIN

    if bd.lot_area_used is None:
        return CoverageLevel.THIN

    has_interrupter = any([
        regime.specific_plan,
        regime.cpio,
        regime.d_limitation,
        regime.q_condition,
    ])
    if has_interrupter:
        return CoverageLevel.PARTIAL

    return CoverageLevel.COMPLETE


def _map_run_status(coverage: CoverageLevel, output: DensityOutput) -> RunStatus:
    dr = output.density_result
    if coverage == CoverageLevel.COMPLETE and dr.status in ("confirmed", "overridden"):
        return RunStatus.OK
    return RunStatus.PARTIAL


def _map_confidence(output: DensityOutput) -> ConfidenceLevel:
    """Map density_result.status to the shared ConfidenceLevel enum."""
    status = output.density_result.status
    if status == "unresolved":
        return ConfidenceLevel.UNRESOLVED
    if status in ("confirmed", "overridden"):
        return ConfidenceLevel.HIGH
    if status == "conflict":
        return ConfidenceLevel.LOW
    return ConfidenceLevel.MEDIUM  # provisional


def _map_blocking(output: DensityOutput) -> bool:
    """Block when no usable density factor exists.

    Analogous to FAR's governing_far.applicable_ratio is None.
    Missing lot area with a known sf_per_du is THIN, not blocking —
    the density factor is still known and reportable.
    """
    return output.density_standard.sf_per_du is None


def _map_action_posture(
    output: DensityOutput,
    blocking: bool,
    mode: str,
    posture: str,
) -> ActionPosture:
    """Derive action posture for comparison or decision-grade mode.

    Priority (highest to lowest):
        1. Blocking — no density factor at all.
        2. Lot area missing — no units computable.
        3. Decision-grade: lane status unresolved → MANUAL_INPUT_REQUIRED.
        4. Authority interrupters present → ACT_ON_DETECTED.
        5. Decision-grade clean → CAN_RELY_WITH_REVIEW.
        6. Comparison mode → ACT_ON_DETECTED (always; lane selection is open).

    Note: in comparison mode, MANUAL_INPUT_REQUIRED fires only for hard data
    gaps, not for missing lane selection. The comparison itself is the deliverable.
    """
    if blocking:
        return ActionPosture.MANUAL_INPUT_REQUIRED

    if output.baseline_density.lot_area_used is None:
        return ActionPosture.MANUAL_INPUT_REQUIRED

    regime = output.parcel_regime
    has_interrupter = any([
        regime.specific_plan, regime.cpio, regime.d_limitation, regime.q_condition
    ])

    if mode == "decision_grade":
        if output.density_result.status == "unresolved":
            return ActionPosture.MANUAL_INPUT_REQUIRED
        if has_interrupter:
            return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
        return ActionPosture.CAN_RELY_WITH_REVIEW

    # Comparison mode: always ACT_ON_DETECTED — the comparison is the answer,
    # not a single resolved number.
    return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS


def _build_plain_language_result(
    baseline_route: dict,
    candidate_routes: list[dict],
    output: DensityOutput,
    posture: str,
    mode: str,
    selected_lane: str | None,
) -> str:
    """Build a concise plain-language summary.

    Comparison mode: baseline anchor + strongest candidate + one caveat.
    Decision-grade: selected route lead + baseline for context + caveat.
    """
    bd = output.baseline_density
    ds = output.density_standard
    regime = output.parcel_regime
    parts: list[str] = []

    if mode == "decision_grade" and selected_lane and selected_lane != "none":
        # Lead with the selected lane result.
        dr = output.density_result
        if dr.claimed_density_is_unlimited:
            units_str = "unlimited"
        elif dr.claimed_density_units is not None:
            units_str = str(dr.claimed_density_units)
        else:
            units_str = "unresolved"
        lane_label = selected_lane.upper().replace("_", " ")
        parts.append(f"Selected lane ({lane_label}): {units_str} units")
        if bd.baseline_units is not None:
            parts.append(f"Baseline (no incentives): {bd.baseline_units} units")
    else:
        # Anchor on baseline — comparison mode or decision-grade with lane=none.
        if bd.baseline_units is not None:
            zone_desc = ds.inherited_zone or regime.base_zone or "unknown zone"
            sf_du_str = f"{ds.sf_per_du} sf/du" if ds.sf_per_du else "?"
            lot_str = f"{bd.lot_area_used:,.0f} sf" if bd.lot_area_used else "?"
            parts.append(
                f"Baseline: {bd.baseline_units} units ({zone_desc}, {sf_du_str}, {lot_str})"
            )
        else:
            parts.append("Baseline: unable to compute")

        if mode == "comparison":
            best = _best_candidate_route(candidate_routes)
            if best is not None:
                lane_label = best["lane"].upper().replace("_", " ")
                if best.get("unlimited"):
                    parts.append(f"{lane_label}: unlimited density (100% affordable path)")
                elif best["units"] is not None and bd.baseline_units is not None:
                    delta = best["units"] - bd.baseline_units
                    parts.append(f"{lane_label}: {best['units']} units (+{delta})")
                elif best["status"] == "requires_more_info":
                    parts.append(f"{lane_label}: requires more information")

    # One caveat sentence — interrupters take priority over posture note.
    interrupters = [
        x for x in [regime.specific_plan, regime.cpio, regime.d_limitation, regime.q_condition]
        if x
    ]
    if interrupters:
        names = ", ".join(interrupters[:2])
        parts.append(f"{names} present — density provisions not reviewed")
    elif posture == "unknown":
        parts.append("Project posture not declared — comparison includes all routes")

    return ". ".join(parts) + "." if parts else "Density determination incomplete."


def _build_summary_str(
    output: DensityOutput,
    coverage: CoverageLevel,
    posture: str,
    mode: str,
) -> str:
    bd = output.baseline_density
    dr = output.density_result
    return (
        f"density: baseline={bd.baseline_units}, "
        f"lane={dr.active_density_lane}, "
        f"status={dr.status}, "
        f"coverage={coverage.value}, "
        f"posture={posture}, "
        f"mode={mode}"
    )


def _build_provenance(output: DensityOutput) -> Provenance:
    authoritative: list[str] = []
    if output.parcel_regime.base_zone:
        authoritative.append("zimas_parcel_data")
    if output.density_standard.sf_per_du is not None:
        authoritative.append("lamc_density_table")
    if output.baseline_density.lot_area_used is not None:
        authoritative.append("lot_area_input")
    lane = output.incentive_lane.selected
    if lane == "toc":
        authoritative.append("toc_guidelines")
    if lane == "state_db":
        authoritative.append("state_density_bonus_law")
    return Provenance(
        source_types=list(authoritative),
        authoritative_sources_used=authoritative,
    )


def _to_module_result(
    site: Site,
    primary_output: DensityOutput,
    candidate_routes: list[dict],
    excluded_routes: list[dict],
    posture: str,
    mode: str,
    selected_lane: str | None,
) -> ModuleResult:
    coverage = _map_coverage_level(primary_output)
    run_status = _map_run_status(coverage, primary_output)
    confidence = _map_confidence(primary_output)
    blocking = _map_blocking(primary_output)
    action_posture = _map_action_posture(primary_output, blocking, mode, posture)

    baseline_route = next(r for r in candidate_routes if r["lane"] == "none")
    plain_language_result = _build_plain_language_result(
        baseline_route, candidate_routes, primary_output, posture, mode, selected_lane
    )
    summary = _build_summary_str(primary_output, coverage, posture, mode)

    bd = primary_output.baseline_density
    ds = primary_output.density_standard
    regime = primary_output.parcel_regime

    selected_route = (
        next((r for r in candidate_routes if r["lane"] == selected_lane), None)
        if selected_lane is not None
        else None
    )

    return ModuleResult(
        module="density",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=blocking,
        inputs_summary={
            "apn": site.apn if hasattr(site, "apn") else None,
            "lot_area_sf": bd.lot_area_used,
            "base_zone": regime.base_zone,
            "height_district": regime.height_district,
            "toc_tier": regime.toc_tier_zimas,
            "specific_plan": regime.specific_plan,
            "cpio": regime.cpio,
            "d_limitation": regime.d_limitation,
            "q_condition": regime.q_condition,
            "development_posture": posture,
            "operating_mode": mode,
            "selected_lane": selected_lane,
        },
        interpretation=Interpretation(
            summary=summary,
            plain_language_result=plain_language_result,
            action_posture=action_posture,
        ),
        provenance=_build_provenance(primary_output),
        module_payload={
            "operating_mode": mode,
            "development_posture": posture,
            "baseline": {
                "units": bd.baseline_units,
                "sf_per_du": ds.sf_per_du,
                "lot_area_sf": bd.lot_area_used,
                "status": bd.status,
            },
            "candidate_routes": candidate_routes,
            "excluded_routes": excluded_routes,
            "selected_route": selected_route,
            "full_output": primary_output.model_dump(),
        },
    )


# ── Public entry point ────────────────────────────────────────────────────────


def run_density_module(
    site: Site,
    project: Project,
    development_posture: str | None = None,
    selected_lane: str | None = None,
    ed1_pathway: bool = False,
) -> ModuleResult:
    """Run the density pipeline and return a standardized ModuleResult.

    Two operating modes:

        Comparison mode (selected_lane=None):
            Runs the baseline plus all posture-relevant candidate lanes.
            Returns a filtered route comparison in module_payload.candidate_routes.
            action_posture is ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS unless
            a hard data gap is present (MANUAL_INPUT_REQUIRED).

        Decision-grade mode (selected_lane provided):
            Runs the full engine for the selected lane.
            Returns a single-lane result suitable for G-sheet output.
            action_posture is CAN_RELY_WITH_REVIEW when the lane is cleanly resolved.

    Args:
        site:                Parsed site data.
        project:             Project assumptions.
        development_posture: Override posture detection. One of:
                             "market_rate", "mixed", "affordable_100", "unknown".
                             If None, derived from project.affordability fields.
        selected_lane:       None = comparison mode.
                             "none" / "toc" / "state_db" = decision-grade mode.
        ed1_pathway:         Whether the ED1 pathway is claimed (decision-grade only).
    """
    posture = derive_development_posture(project, override=development_posture)

    if selected_lane is not None:
        # Decision-grade: run the engine for the selected lane.
        mode = "decision_grade"
        primary_output = run_density(
            site, project,
            incentive_lane=selected_lane,
            ed1_pathway=ed1_pathway,
            lane_selected_by="user",
        )
        baseline_route = _build_baseline_route_dict(primary_output)
        if selected_lane == "none":
            candidate_routes: list[dict] = [baseline_route]
        else:
            selected_route_dict = _run_lane(site, project, selected_lane, ed1_pathway)
            candidate_routes = [baseline_route, selected_route_dict]
        excluded_routes: list[dict] = []
    else:
        # Comparison: run baseline + all posture-relevant candidate lanes.
        mode = "comparison"
        primary_output = run_density(
            site, project,
            incentive_lane="none",
            lane_selected_by="auto",
        )
        regime = primary_output.parcel_regime
        candidate_lane_list, excluded_routes = _candidate_lanes_and_excluded(posture, regime)
        baseline_route = _build_baseline_route_dict(primary_output)
        candidate_routes = [baseline_route]
        for lane in candidate_lane_list:
            if lane != "none":
                candidate_routes.append(_run_lane(site, project, lane, ed1_pathway))

    return _to_module_result(
        site=site,
        primary_output=primary_output,
        candidate_routes=candidate_routes,
        excluded_routes=excluded_routes,
        posture=posture,
        mode=mode,
        selected_lane=selected_lane,
    )
