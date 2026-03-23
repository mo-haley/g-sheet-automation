"""Dedication screen orchestrator.

Thin orchestrator that validates inputs, calls per-frontage screening,
aggregates site-level results, and wraps into ModuleResult.

Pipeline:
    Step 1: Validate inputs
    Steps 2-6: Per-frontage screening (delegation to screen.py)
    Step 7: Site-level aggregation
    Step 8: Action posture assignment

Entry points:
    run_dedication_screen(inputs) -> DedicationScreenPayload
    run_dedication_screen_module(inputs) -> ModuleResult
"""

from __future__ import annotations

from dedication_screen.models import (
    DISCLAIMER,
    DedicationIssue,
    DedicationScreenInput,
    DedicationScreenPayload,
    FrontageResult,
    ScreeningConfidence,
    ScreeningStatus,
    SiteSummary,
)
from dedication_screen.screen import screen_frontage
from dedication_screen.standards import STANDARDS_TABLE_VERSION
from models.result_common import (
    ActionPosture,
    ConfidenceLevel,
    CoverageLevel,
    Interpretation,
    ModuleResult,
    Provenance,
    RunStatus,
)


# -- Step 1: Validate inputs --------------------------------------------------


def _validate_inputs(inputs: DedicationScreenInput) -> list[DedicationIssue]:
    """Validate top-level inputs. Returns issues list."""
    issues: list[DedicationIssue] = []
    if not inputs.frontages:
        issues.append(DedicationIssue(
            step="input_validation",
            field="frontages",
            severity="error",
            message="No frontages provided. Cannot perform dedication screening.",
            action_required="Provide at least one frontage.",
            confidence_impact="degrades_to_unresolved",
        ))
    for f in inputs.frontages:
        if not f.street_name or not f.street_name.strip():
            issues.append(DedicationIssue(
                step="input_validation",
                field="street_name",
                severity="error",
                message=f"Frontage '{f.edge_id}' has empty street_name.",
                action_required="Provide street name for this frontage.",
                confidence_impact="degrades_to_unresolved",
            ))
    return issues


# -- Step 7: Site aggregation --------------------------------------------------


def _aggregate_site(
    frontage_results: list[FrontageResult],
    gross_lot_area_sf: float | None,
) -> SiteSummary:
    """Aggregate per-frontage results into site-level summary."""
    if not frontage_results:
        return SiteSummary()

    # Area aggregation
    calculable_areas = [
        fr.estimated_dedication_area_sf
        for fr in frontage_results
        if fr.estimated_dedication_area_sf is not None
    ]
    all_have_area = all(
        fr.estimated_dedication_area_sf is not None
        for fr in frontage_results
    )

    if calculable_areas:
        total_area = sum(calculable_areas)
        partial = not all_have_area
    else:
        total_area = None
        partial = False

    # Status: worst-case across frontages
    status_priority = [
        ScreeningStatus.MANUAL_REVIEW_REQUIRED,
        ScreeningStatus.LIKELY_DEDICATION,
        ScreeningStatus.POSSIBLE_DEDICATION,
        ScreeningStatus.NO_APPARENT_DEDICATION,
    ]
    worst_status = ScreeningStatus.NO_APPARENT_DEDICATION
    for fr in frontage_results:
        if status_priority.index(fr.frontage_status) < status_priority.index(worst_status):
            worst_status = fr.frontage_status

    # Confidence: worst-case across frontages
    conf_priority = [
        ScreeningConfidence.UNRESOLVED,
        ScreeningConfidence.LOW,
        ScreeningConfidence.MEDIUM,
        ScreeningConfidence.HIGH,
    ]
    worst_conf = ScreeningConfidence.HIGH
    for fr in frontage_results:
        if conf_priority.index(fr.frontage_confidence) < conf_priority.index(worst_conf):
            worst_conf = fr.frontage_confidence

    manual_review_count = sum(
        1 for fr in frontage_results
        if fr.frontage_status == ScreeningStatus.MANUAL_REVIEW_REQUIRED
    )

    # Collect manual review reasons
    manual_reasons: list[str] = []
    for fr in frontage_results:
        if fr.frontage_status == ScreeningStatus.MANUAL_REVIEW_REQUIRED:
            for issue in fr.issues:
                if issue.action_required:
                    reason = f"{fr.street_name}: {issue.message}"
                    if reason not in manual_reasons:
                        manual_reasons.append(reason)
        for flag in fr.complexity_flags:
            reason = f"{fr.street_name}: {flag}"
            if reason not in manual_reasons:
                manual_reasons.append(reason)

    # Adjusted lot area
    adjusted = None
    if gross_lot_area_sf is not None and total_area is not None:
        adjusted = gross_lot_area_sf - total_area

    return SiteSummary(
        total_estimated_dedication_area_sf=total_area,
        dedication_area_is_partial=partial,
        any_dedication_likely=any(
            fr.frontage_status == ScreeningStatus.LIKELY_DEDICATION
            for fr in frontage_results
        ),
        all_frontages_screened=manual_review_count == 0,
        frontages_requiring_manual_review=manual_review_count,
        site_status=worst_status,
        site_confidence=worst_conf,
        adjusted_lot_area_sf=adjusted,
        manual_review_reasons=manual_reasons,
    )


# -- Step 8: Action posture ---------------------------------------------------


def _resolve_action_posture(
    frontage_results: list[FrontageResult],
) -> ActionPosture:
    """Determine site-level action posture per locked spec.

    Precedence (strict):
        1. MANUAL_INPUT_REQUIRED — any frontage missing apparent current condition
        2. AUTHORITY_CONFIRMATION_REQUIRED — any nonzero shortfall OR forced-manual-review
        3. CAN_RELY_WITH_REVIEW — all clean
    """
    any_missing_input = any(
        fr.apparent_condition_source == "unresolved"
        for fr in frontage_results
    )
    if any_missing_input:
        return ActionPosture.MANUAL_INPUT_REQUIRED

    any_nonzero_shortfall = any(
        fr.screening_shortfall_ft is not None and fr.screening_shortfall_ft > 0
        for fr in frontage_results
    )
    any_forced_manual = any(
        fr.frontage_status == ScreeningStatus.MANUAL_REVIEW_REQUIRED
        and fr.apparent_condition_source != "unresolved"
        for fr in frontage_results
    )
    if any_nonzero_shortfall or any_forced_manual:
        return ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED

    return ActionPosture.CAN_RELY_WITH_REVIEW


# -- Core pipeline runner ------------------------------------------------------


def run_dedication_screen(
    inputs: DedicationScreenInput,
) -> DedicationScreenPayload:
    """Run the dedication screening pipeline.

    Returns DedicationScreenPayload with per-frontage results and
    site-level summary.
    """
    validation_issues = _validate_inputs(inputs)

    # Blocked: no frontages
    if not inputs.frontages:
        summary = SiteSummary()
        return DedicationScreenPayload(
            standards_table_version=STANDARDS_TABLE_VERSION,
            frontage_results=[],
            site_summary=summary,
        )

    # Screen each frontage
    frontage_results: list[FrontageResult] = []
    for frontage in inputs.frontages:
        fr = screen_frontage(
            frontage=frontage,
            tolerances=inputs.tolerances,
            lot_type=inputs.lot_type,
            num_frontages=len(inputs.frontages),
        )
        # Attach any validation issues for this frontage
        for vi in validation_issues:
            if vi.field == "street_name" and frontage.edge_id in vi.message:
                fr.issues.append(vi)
                fr.frontage_status = ScreeningStatus.MANUAL_REVIEW_REQUIRED
        frontage_results.append(fr)

    # Aggregate
    site_summary = _aggregate_site(frontage_results, inputs.gross_lot_area_sf)

    return DedicationScreenPayload(
        standards_table_version=STANDARDS_TABLE_VERSION,
        frontage_results=frontage_results,
        site_summary=site_summary,
    )


# -- ModuleResult adapter helpers ----------------------------------------------


def _map_coverage_level(payload: DedicationScreenPayload) -> CoverageLevel:
    """Map screening results to CoverageLevel.

    NONE:      no frontages provided
    UNCERTAIN: all frontages are MANUAL_REVIEW_REQUIRED
    THIN:      some frontages screened, some are MANUAL_REVIEW_REQUIRED
    PARTIAL:   all frontages screened but some have POSSIBLE_DEDICATION
    COMPLETE:  all frontages screened with definitive status
    """
    if not payload.frontage_results:
        return CoverageLevel.NONE

    manual_count = payload.site_summary.frontages_requiring_manual_review
    total = len(payload.frontage_results)

    if manual_count == total:
        return CoverageLevel.UNCERTAIN
    if manual_count > 0:
        return CoverageLevel.THIN
    if any(
        fr.frontage_status == ScreeningStatus.POSSIBLE_DEDICATION
        for fr in payload.frontage_results
    ):
        return CoverageLevel.PARTIAL
    return CoverageLevel.COMPLETE


def _map_run_status(coverage: CoverageLevel) -> RunStatus:
    if coverage == CoverageLevel.NONE:
        return RunStatus.BLOCKED
    if coverage == CoverageLevel.UNCERTAIN:
        return RunStatus.BLOCKED
    if coverage == CoverageLevel.COMPLETE:
        return RunStatus.OK
    return RunStatus.PARTIAL


def _map_confidence(payload: DedicationScreenPayload) -> ConfidenceLevel:
    conf = payload.site_summary.site_confidence
    mapping = {
        ScreeningConfidence.HIGH: ConfidenceLevel.HIGH,
        ScreeningConfidence.MEDIUM: ConfidenceLevel.MEDIUM,
        ScreeningConfidence.LOW: ConfidenceLevel.LOW,
        ScreeningConfidence.UNRESOLVED: ConfidenceLevel.UNRESOLVED,
    }
    return mapping.get(conf, ConfidenceLevel.UNRESOLVED)


def _map_blocking(coverage: CoverageLevel) -> bool:
    return coverage in (CoverageLevel.NONE, CoverageLevel.UNCERTAIN)


def _build_plain_language_result(
    payload: DedicationScreenPayload,
    action_posture: ActionPosture,
) -> str:
    """Build plain-language summary from screening results."""
    if not payload.frontage_results:
        return "No frontages provided. Cannot perform dedication screening."

    parts: list[str] = []

    for fr in payload.frontage_results:
        if fr.frontage_status == ScreeningStatus.NO_APPARENT_DEDICATION:
            parts.append(
                f"{fr.street_name} ({fr.designation_class or 'unknown'}): "
                f"no apparent dedication"
            )
        elif fr.frontage_status == ScreeningStatus.POSSIBLE_DEDICATION:
            parts.append(
                f"{fr.street_name} ({fr.designation_class or 'unknown'}): "
                f"possible dedication ~{fr.estimated_dedication_depth_ft:.1f} ft "
                f"(within screening tolerance)"
            )
        elif fr.frontage_status == ScreeningStatus.LIKELY_DEDICATION:
            area_str = ""
            if fr.estimated_dedication_area_sf is not None:
                area_str = f", ~{fr.estimated_dedication_area_sf:.0f} SF"
            parts.append(
                f"{fr.street_name} ({fr.designation_class or 'unknown'}): "
                f"likely dedication ~{fr.estimated_dedication_depth_ft:.1f} ft"
                f"{area_str}"
            )
        elif fr.frontage_status == ScreeningStatus.MANUAL_REVIEW_REQUIRED:
            if fr.apparent_condition_source == "unresolved":
                parts.append(
                    f"{fr.street_name}: apparent current half-ROW not provided"
                )
            elif fr.designation_source == "unresolved":
                parts.append(
                    f"{fr.street_name}: street designation not resolved"
                )
            else:
                parts.append(
                    f"{fr.street_name}: manual review required"
                )

    summary = payload.site_summary
    if summary.total_estimated_dedication_area_sf is not None:
        partial_note = " (partial)" if summary.dedication_area_is_partial else ""
        parts.append(
            f"Total estimated dedication area: "
            f"~{summary.total_estimated_dedication_area_sf:.0f} SF{partial_note}"
        )
    if summary.adjusted_lot_area_sf is not None:
        parts.append(
            f"Adjusted lot area (screening): "
            f"~{summary.adjusted_lot_area_sf:.0f} SF"
        )

    parts.append(
        "This is a screening estimate — final dedication requires "
        "BOE determination."
    )

    return ". ".join(parts) + "." if parts else "Dedication screening incomplete."


def _build_summary_str(payload: DedicationScreenPayload) -> str:
    n = len(payload.frontage_results)
    status = payload.site_summary.site_status.value
    manual = payload.site_summary.frontages_requiring_manual_review
    if n == 0:
        return "Dedication screen: no frontages provided."
    if manual == n:
        return f"Dedication screen: {n} frontage(s), all require manual review."
    area = payload.site_summary.total_estimated_dedication_area_sf
    if area is not None:
        return (
            f"Dedication screen: {n} frontage(s), site_status={status}, "
            f"~{area:.0f} SF estimated dedication."
        )
    return f"Dedication screen: {n} frontage(s), site_status={status}."


def _build_provenance() -> Provenance:
    return Provenance(
        source_types=["street_designation_standards"],
        authoritative_sources_used=["mobility_plan_2035_standards_table"],
        non_authoritative_sources_used=["user_reported_apparent_row"],
        notes=(
            "Standards table is a v0 convenience lookup. "
            "Apparent current ROW is user-reported, not surveyed."
        ),
    )


def _build_inputs_summary(inputs: DedicationScreenInput) -> dict:
    d: dict = {
        "lot_type": inputs.lot_type,
        "num_frontages": len(inputs.frontages),
        "screening_tolerance_ft": inputs.tolerances.screening_tolerance_ft,
    }
    if inputs.parcel_apn:
        d["parcel_apn"] = inputs.parcel_apn
    if inputs.parcel_address:
        d["parcel_address"] = inputs.parcel_address
    if inputs.gross_lot_area_sf is not None:
        d["gross_lot_area_sf"] = inputs.gross_lot_area_sf
    return d


def _build_module_payload(payload: DedicationScreenPayload) -> dict:
    return payload.model_dump()


# -- Public entry point --------------------------------------------------------


def run_dedication_screen_module(
    inputs: DedicationScreenInput,
) -> ModuleResult:
    """Run dedication screening pipeline and return a standardized ModuleResult.

    This is the primary entry point for pipeline integration.
    """
    payload = run_dedication_screen(inputs)
    action_posture = _resolve_action_posture(payload.frontage_results)

    coverage = _map_coverage_level(payload)
    run_status = _map_run_status(coverage)
    confidence = _map_confidence(payload)
    blocking = _map_blocking(coverage)

    plain_language = _build_plain_language_result(payload, action_posture)
    summary = _build_summary_str(payload)

    return ModuleResult(
        module="dedication_screen",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=blocking,
        inputs_summary=_build_inputs_summary(inputs),
        interpretation=Interpretation(
            summary=summary,
            plain_language_result=plain_language,
            action_posture=action_posture,
        ),
        provenance=_build_provenance(),
        module_payload=_build_module_payload(payload),
    )
