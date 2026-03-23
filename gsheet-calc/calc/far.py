"""FAR calculation orchestrator."""

from __future__ import annotations

from models.far_output import FAROutput
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.far import FARRule

from models.result_common import (
    ModuleResult,
    RunStatus,
    CoverageLevel,
    ConfidenceLevel,
    ActionPosture,
    Interpretation,
    Provenance,
)


def calculate_far(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the FAR calculation (legacy interface)."""
    rule = FARRule()
    return rule.evaluate(site, project)


def calculate_far_full(site: Site, project: Project) -> FAROutput:
    """Run the full FAR determination, returning the structured FAROutput."""
    rule = FARRule()
    return rule.evaluate_full(site, project)


# ── ModuleResult adapter ──────────────────────────────────────────────────────


def _map_coverage_level(output: FAROutput) -> CoverageLevel:
    """Derive coverage level from FAR output in priority order (first match wins).

    Coverage describes how complete the inputs are across both dimensions:
        allowable-side: zone → baseline → governing FAR
        proposed-side:  architect's counted floor area
    """
    # Uncertain: zone string parse explicitly failed
    if output.zoning.parse_confidence == "unresolved":
        return CoverageLevel.UNCERTAIN

    # Thin: fundamental inputs missing — can't attempt the calculation
    if output.parcel.identity_confidence == "unresolved":
        return CoverageLevel.THIN
    if output.baseline_far.ratio is None:
        return CoverageLevel.THIN

    # Partial: data present but gaps prevent a confirmed answer
    if output.governing_far.state == "unresolved":
        return CoverageLevel.PARTIAL
    if output.proposed.numerator_source == "unresolved":
        return CoverageLevel.PARTIAL

    return CoverageLevel.COMPLETE


def _map_run_status(coverage: CoverageLevel, output: FAROutput) -> RunStatus:
    if (
        output.outcome.confidence == "high"
        and output.proposed.numerator_source != "unresolved"
        and coverage == CoverageLevel.COMPLETE
    ):
        return RunStatus.OK
    return RunStatus.PARTIAL


def _map_confidence(output: FAROutput) -> ConfidenceLevel:
    """Two-axis minimum over the allowable side and the proposed side."""
    # Allowable side unresolvable
    if output.outcome.state == "unresolved":
        return ConfidenceLevel.UNRESOLVED

    allowable_conf = output.outcome.confidence  # "high" / "medium" / "low"
    numerator_src = output.proposed.numerator_source
    numerator_conf = output.proposed.numerator_confidence  # "high" / "medium" / "low"

    if allowable_conf == "low":
        return ConfidenceLevel.LOW

    if allowable_conf == "medium":
        return ConfidenceLevel.MEDIUM

    # allowable_conf == "high" — take the minimum with the proposed side
    if numerator_src == "unresolved":
        # Know the allowable limit but cannot compute proposed — useful but incomplete
        return ConfidenceLevel.LOW

    if numerator_conf == "high":
        return ConfidenceLevel.HIGH

    if numerator_conf == "medium":
        return ConfidenceLevel.MEDIUM

    return ConfidenceLevel.LOW


def _map_blocking(output: FAROutput) -> bool:
    """Block only when no usable governing FAR figure exists.

    baseline_with_override_risk and other provisional states still carry
    a ratio — they are PARTIAL, not blocking.
    """
    return output.governing_far.applicable_ratio is None


def _map_action_posture(output: FAROutput, blocking: bool) -> ActionPosture:
    if blocking:
        if output.local_controls.override_present:
            # Specific plan / CPIO / D ordinance present but not parsed — need authority doc
            return ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
        # Parse or identity failure — need user to supply the missing input data
        return ActionPosture.MANUAL_INPUT_REQUIRED

    if output.proposed.numerator_source == "unresolved":
        # Allowable FAR is known; architect must provide counted floor area
        return ActionPosture.MANUAL_INPUT_REQUIRED

    if output.outcome.requires_manual_review or output.proposed.definition_aligned is False:
        return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS

    return ActionPosture.CAN_RELY_WITH_REVIEW


def _build_provenance(site: Site, output: FAROutput) -> Provenance:
    authoritative: list[str] = []

    if output.zoning.base_zone or output.parcel.lot_area_sf:
        authoritative.append("zimas_parcel_data")
    if output.baseline_far.ratio is not None:
        authoritative.append("lamc_far_table_2")
    if output.proposed.numerator_source in ("explicit_total", "per_floor_entries"):
        authoritative.append("architect_floor_area_input")

    return Provenance(
        source_types=list(authoritative),
        authoritative_sources_used=authoritative,
    )


def _build_summary(output: FAROutput, coverage: CoverageLevel) -> str:
    gov = output.governing_far
    ratio_str = f"{gov.applicable_ratio}:1" if gov.applicable_ratio is not None else "unresolved"
    return (
        f"far: governing={ratio_str}, "
        f"state={gov.state}, "
        f"coverage={coverage.value}, "
        f"numerator={output.proposed.numerator_source}"
    )


def _to_module_result(site: Site, project: object, output: FAROutput) -> ModuleResult:
    coverage = _map_coverage_level(output)
    run_status = _map_run_status(coverage, output)
    confidence = _map_confidence(output)
    blocking = _map_blocking(output)
    action_posture = _map_action_posture(output, blocking)

    # plain_language_result: synthesize the key facts a caller needs at a glance
    gov = output.governing_far
    prop = output.proposed
    parts: list[str] = []

    if gov.applicable_ratio is not None:
        parts.append(f"Governing FAR: {gov.applicable_ratio}:1 ({gov.state})")
    else:
        parts.append("Governing FAR: unresolved")

    if output.allowable.governing_floor_area_sf is not None:
        parts.append(f"Allowable floor area: {output.allowable.governing_floor_area_sf:,.0f} sf")

    if prop.numerator_source != "unresolved" and prop.far_ratio is not None:
        compliance = "compliant" if prop.compliant else "non-compliant" if prop.compliant is False else "unknown"
        parts.append(f"Proposed FAR: {prop.far_ratio:.2f}:1 ({compliance})")
    elif prop.numerator_source == "unresolved":
        parts.append("Proposed FAR: not provided by architect")

    if output.outcome.requires_manual_review:
        parts.append(f"Manual review required: {'; '.join(output.outcome.manual_review_reasons) or 'see issues'}")

    plain_language_result = ". ".join(parts) + "." if parts else "FAR determination incomplete."

    return ModuleResult(
        module="far",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=blocking,
        inputs_summary={
            "apn": site.apn if hasattr(site, "apn") else None,
            "lot_area_sf": output.parcel.lot_area_sf,
            "base_zone": output.zoning.base_zone,
            "height_district": output.zoning.height_district,
            "parse_confidence": output.zoning.parse_confidence,
            "specific_plan": output.local_controls.specific_plan,
            "cpio": output.local_controls.cpio,
            "d_limitation": output.local_controls.d_limitation,
            "q_condition": output.local_controls.q_condition,
            "numerator_source": output.proposed.numerator_source,
            "definition_aligned": output.proposed.definition_aligned,
        },
        interpretation=Interpretation(
            summary=_build_summary(output, coverage),
            plain_language_result=plain_language_result,
            action_posture=action_posture,
        ),
        provenance=_build_provenance(site, output),
        module_payload=output.model_dump(),
    )


def calculate_far_module(site: Site, project: Project) -> ModuleResult:
    """Run the full FAR determination and return a standardized ModuleResult.

    The full FAROutput is preserved in result.module_payload.
    The internal 10-step engine is unchanged — this is a thin wrapper.
    """
    output = calculate_far_full(site, project)
    return _to_module_result(site, project, output)
