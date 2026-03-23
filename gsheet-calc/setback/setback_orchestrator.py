"""Setback orchestrator (Step 6 of 6 — final module).

Calls the five pipeline steps in order and assembles SetbackOutput.
Contains no business logic — all decision logic lives in the step modules.

Pipeline order:
  1. setback_authority   → SetbackAuthorityResult
  2. setback_yard_family → YardFamilyResult
  3. setback_edge_classifier → list[ClassifiedEdge]
  4. setback_edge_calc   → list[EdgeResult]
  5. setback_status      → SetbackResult

═══════════════════════════════════════════════
CALLER CONTRACT
═══════════════════════════════════════════════

Callers provide:
  - SetbackProjectInputs (lot geometry, edges, use_mix, story count, etc.)
  - Raw zone string and base zone (e.g. "C2-1VL", "C2")
  - Optional overlay flags (specific_plan, cpio, d_limitation, q_condition)
  - chapter_1a_applicable (True / False / None)

Callers must:
  - Supply edge_type="interior_rear" on EdgeInputs for lots with no alley access
    when the rear lot line is known. Without this hint, all interior edges on
    non-alley lots return manual_confirm classification.
  - Populate per_edge_adjacency for each edge where adjacency zone is known.
    Edges absent from the dict receive adjacency_status="not_provided" and
    no adjacency adjustment is attempted.

Callers must NOT:
  - Expect a single governing setback number — the result is per-edge.
  - Expect a prevailing front yard to be calculated — prevailing_setback_flag=True
    on all front edges signals that manual site analysis is required.
  - Auto-select a CM yard option — both options are present in
    yard_family_result.cm_options and all CM edges carry status="split_condition".

Adapter entry point:
    run_setback_module(...) -> ModuleResult

Legacy entry point (untouched behavior):
    run_setback(...) -> SetbackResult
"""

from __future__ import annotations

from models.result_common import (
    ActionPosture,
    ConfidenceLevel,
    CoverageLevel,
    Interpretation,
    ModuleResult,
    Provenance,
    RunStatus,
)
from setback.models import (
    SetbackOutput,
    SetbackProjectInputs,
    SetbackResult,
)
from setback.setback_authority import resolve_setback_authority
from setback.setback_edge_calc import calculate_edge_yards
from setback.setback_edge_classifier import classify_edges
from setback.setback_status import build_setback_result
from setback.setback_yard_family import get_yard_family_rules


# ── Pipeline ──────────────────────────────────────────────────────────────────


def _build_setback_output(
    *,
    project_inputs: SetbackProjectInputs,
    raw_zone: str,
    base_zone: str,
    height_district: str | None = None,
    specific_plan: bool = False,
    cpio: bool = False,
    d_limitation: bool = False,
    q_condition: bool = False,
    chapter_1a_applicable: bool | None = None,
) -> SetbackOutput:
    """Run pipeline steps 1–4 and return the assembled SetbackOutput.

    This is the shared internal pipeline runner used by both run_setback()
    and run_setback_module(). It does not call build_setback_result().
    """
    # ── Step 1: Authority ────────────────────────────────────────────────────
    authority_result = resolve_setback_authority(
        raw_zone=raw_zone,
        base_zone=base_zone,
        height_district=height_district,
        specific_plan=specific_plan,
        cpio=cpio,
        d_limitation=d_limitation,
        q_condition=q_condition,
        chapter_1a_applicable=chapter_1a_applicable,
        small_lot_subdivision=project_inputs.small_lot_subdivision,
    )

    # ── Step 2: Yard family ──────────────────────────────────────────────────
    yard_family_result = get_yard_family_rules(authority_result)

    # ── Step 3: Edge classifier ──────────────────────────────────────────────
    classified_edges = classify_edges(
        lot_type=project_inputs.lot_type,
        lot_geometry_regular=project_inputs.lot_geometry_regular,
        edges=project_inputs.edges,
    )

    # ── Step 4: Edge calc ────────────────────────────────────────────────────
    edge_results = calculate_edge_yards(
        authority_result=authority_result,
        yard_family_result=yard_family_result,
        classified_edges=classified_edges,
        project_inputs=project_inputs,
    )

    return SetbackOutput(
        inputs=project_inputs,
        authority_result=authority_result,
        yard_family_result=yard_family_result,
        classified_edges=classified_edges,
        edge_results=edge_results,
    )


def run_setback(
    *,
    project_inputs: SetbackProjectInputs,
    raw_zone: str,
    base_zone: str,
    height_district: str | None = None,
    specific_plan: bool = False,
    cpio: bool = False,
    d_limitation: bool = False,
    q_condition: bool = False,
    chapter_1a_applicable: bool | None = None,
) -> SetbackResult:
    """Run the full setback pipeline for one project.

    Args:
        project_inputs:       Lot/building/edge inputs (SetbackProjectInputs).
        raw_zone:             Raw zone string from the assessor or plan check
                              (e.g. "C2-1VL", "[Q]R3-1", "RAS3-1").
        base_zone:            Base zone code without suffix
                              (e.g. "C2", "R3", "RAS3").
        height_district:      Height district string if known (e.g. "1VL").
                              Currently deferred — passed through for future use.
        specific_plan:        True if a specific plan applies to this parcel.
        cpio:                 True if a CPIO applies to this parcel.
        d_limitation:         True if a D limitation is in effect.
        q_condition:          True if a Q condition is in effect.
        chapter_1a_applicable: True / False / None (unknown).

    Returns:
        SetbackResult with per-edge yard chains, overall_status,
        authority_chain_summary, and aggregated issues / review reasons.

    Raises:
        Nothing — all error conditions are captured as issues / unresolved
        status values in the output. The orchestrator does not raise.
    """
    output = _build_setback_output(
        project_inputs=project_inputs,
        raw_zone=raw_zone,
        base_zone=base_zone,
        height_district=height_district,
        specific_plan=specific_plan,
        cpio=cpio,
        d_limitation=d_limitation,
        q_condition=q_condition,
        chapter_1a_applicable=chapter_1a_applicable,
    )
    return build_setback_result(output)


# ── ModuleResult adapter ───────────────────────────────────────────────────────


def _map_coverage_level(result: SetbackResult) -> CoverageLevel:
    """Map SetbackResult to CoverageLevel.

    UNCERTAIN: early_exit triggered OR zone not found (code_family is None).
              No usable values computed.
    THIN:     Zone resolved but no edges. Module ran cleanly; caller must
              supply lot-line geometry before values can be produced.
    PARTIAL:  Edges present but overall_status is provisional, split_condition,
              or unresolved. Baseline values available but governing uncertain.
    COMPLETE: overall_status confirmed or overridden. Zone, family, and all
              edge calcs resolved with no open interrupters.
    """
    if result.early_exit.triggered or result.code_family is None:
        return CoverageLevel.UNCERTAIN
    if not result.edges:
        return CoverageLevel.THIN
    if result.overall_status in ("confirmed", "overridden"):
        return CoverageLevel.COMPLETE
    return CoverageLevel.PARTIAL


def _map_run_status(coverage: CoverageLevel) -> RunStatus:
    """Map coverage to RunStatus.

    BLOCKED: UNCERTAIN coverage (no usable values; blocking=True required).
    OK:      COMPLETE coverage. Unlike density/parking, setbacks have no
             "prior entitlement" gap — confirmed edges are genuinely confirmed.
    PARTIAL: all other cases (THIN, PARTIAL).
    """
    if coverage == CoverageLevel.UNCERTAIN:
        return RunStatus.BLOCKED
    if coverage == CoverageLevel.COMPLETE:
        return RunStatus.OK
    return RunStatus.PARTIAL


def _map_blocking(result: SetbackResult) -> bool:
    """True only when no usable values were computed.

    THIN (zone resolved, no edges) → False. Zone lookup succeeded; the
    caller just needs to supply edge geometry.
    """
    return result.early_exit.triggered or result.code_family is None


def _map_confidence(result: SetbackResult) -> ConfidenceLevel:
    status = result.overall_status
    if status == "confirmed":
        return ConfidenceLevel.HIGH
    if status in ("provisional", "overridden"):
        return ConfidenceLevel.MEDIUM
    if status == "split_condition":
        return ConfidenceLevel.LOW
    # "unresolved" — distinguish hard-blocked vs. edges-but-uncertain
    if result.early_exit.triggered or result.code_family is None:
        return ConfidenceLevel.UNRESOLVED
    return ConfidenceLevel.LOW


def _map_action_posture(result: SetbackResult, output: SetbackOutput) -> ActionPosture:
    """Priority chain (highest → lowest):

    1. early_exit / no code_family / no edges → MANUAL_INPUT_REQUIRED
    2. split_condition (CM/RAS — two yard families, cannot auto-select) → MANUAL_INPUT_REQUIRED
    3. sources_flagged_not_interpreted non-empty (SP/CPIO/D/Q) → AUTHORITY_CONFIRMATION_REQUIRED
    4. manual_review_reasons non-empty → ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    5. else → CAN_RELY_WITH_REVIEW
    """
    if result.early_exit.triggered or result.code_family is None or not result.edges:
        return ActionPosture.MANUAL_INPUT_REQUIRED
    if result.overall_status == "split_condition":
        return ActionPosture.MANUAL_INPUT_REQUIRED
    if result.sources_flagged_not_interpreted:
        return ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
    if result.manual_review_reasons:
        return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS
    return ActionPosture.CAN_RELY_WITH_REVIEW


def _edge_value_str(edge) -> str:
    """Format one edge's governing yard for the plain_language_result."""
    if edge.prevailing_setback_flag:
        return f"{edge.edge_id} ({edge.edge_classification}): prevailing (manual)"
    if edge.governing_yard_ft is not None:
        return f"{edge.edge_id} ({edge.edge_classification}): {edge.governing_yard_ft:.1f}ft"
    return f"{edge.edge_id} ({edge.edge_classification}): unresolved"


def _build_plain_language_result(result: SetbackResult, output: SetbackOutput) -> str:
    """Three locked cases plus early_exit and zone-failure fast paths."""
    # Early exit
    if result.early_exit.triggered:
        return f"Setback not computed: {result.early_exit.reason}"

    # Zone not found
    if result.code_family is None:
        return (
            "Zone not found in setback table. "
            "Cannot determine yard family; no setback values computed."
        )

    baseline = output.authority_result.baseline_yard_family or "(unknown)"

    # No-edge THIN case
    if not result.edges:
        return (
            f"Baseline yard family: {baseline}. "
            "No lot edges provided — setback values cannot be computed until edges are supplied."
        )

    n = len(result.edges)

    # Split-condition case (CM/RAS)
    if result.overall_status == "split_condition":
        return (
            f"Baseline yard family: {baseline} (split). "
            "Governing yard family: unresolved — use type determines which yard standard applies. "
            f"{n} edge(s) carry provisional values pending family confirmation."
        )

    # Authority interrupters (provisional)
    if result.sources_flagged_not_interpreted:
        interrupter_sources = [
            i.source for i in output.authority_result.authority_interrupters
        ]
        flagged_str = ", ".join(interrupter_sources) if interrupter_sources else "overlays"
        governing = result.inherited_yard_family or baseline
        return (
            f"Baseline yard family: {baseline}. "
            f"Governing yard family: unconfirmed — {flagged_str} detected but not reviewed. "
            f"{n} edge(s) computed with baseline values; governing may differ."
        )

    # Clean confirmed/overridden case
    governing = result.inherited_yard_family or baseline
    edge_parts = [_edge_value_str(e) for e in result.edges]
    edges_str = "; ".join(edge_parts)
    return f"Governing yard family: {governing}. {n} edge(s): {edges_str}."


def _build_summary_str(result: SetbackResult, output: SetbackOutput) -> str:
    if result.early_exit.triggered:
        return f"Setbacks: early exit — {result.early_exit.reason}"
    if result.code_family is None:
        return "Setbacks: zone not found; no values computed."
    if not result.edges:
        baseline = output.authority_result.baseline_yard_family or "unknown"
        return f"Setbacks: baseline family {baseline}; no edges supplied."
    n = len(result.edges)
    status = result.overall_status
    governing = result.inherited_yard_family
    if governing:
        return f"Setbacks: {n} edge(s), governing family {governing} ({status})."
    return f"Setbacks: {n} edge(s), governing family unresolved ({status})."


def _build_provenance(result: SetbackResult) -> Provenance:
    notes: str | None = None
    if result.sources_not_checked:
        notes = "Not checked: " + "; ".join(result.sources_not_checked)
    return Provenance(
        authoritative_sources_used=result.sources_checked,
        notes=notes,
    )


def _build_inputs_summary(
    base_zone: str,
    raw_zone: str,
    project_inputs: SetbackProjectInputs,
    specific_plan: str | None,
    cpio: str | None,
    d_limitation: str | None,
    q_condition: str | None,
) -> dict:
    d: dict = {
        "base_zone": base_zone,
        "raw_zone": raw_zone,
        "number_of_edges": len(project_inputs.edges),
        "small_lot_subdivision": project_inputs.small_lot_subdivision,
    }
    if specific_plan:
        d["specific_plan"] = specific_plan
    if cpio:
        d["cpio"] = cpio
    if d_limitation:
        d["d_limitation"] = d_limitation
    if q_condition:
        d["q_condition"] = q_condition
    return d


def _build_module_payload(result: SetbackResult, output: SetbackOutput) -> dict:
    """Preserve per-edge setbacks. No single governing scalar is produced."""
    edges_payload = [
        {
            "edge_id": e.edge_id,
            "classification": e.edge_classification,
            "baseline_yard_ft": e.baseline_yard_ft,
            "governing_yard_ft": e.governing_yard_ft,
            "status": e.status,
            "prevailing_setback_flag": e.prevailing_setback_flag,
            "manual_review_reasons": e.manual_review_reasons,
        }
        for e in result.edges
    ]
    return {
        "overall_status": result.overall_status,
        "baseline_yard_family": output.authority_result.baseline_yard_family,
        "governing_yard_family": result.inherited_yard_family,
        "cm_split": output.authority_result.cm_split,
        "ras_split": output.authority_result.ras_split,
        "authority_interrupters": [
            i.source for i in output.authority_result.authority_interrupters
        ],
        "edges": edges_payload,
        "full_output": result.model_dump(),
    }


def run_setback_module(
    *,
    project_inputs: SetbackProjectInputs,
    raw_zone: str,
    base_zone: str,
    height_district: str | None = None,
    specific_plan: str | None = None,
    cpio: str | None = None,
    d_limitation: str | None = None,
    q_condition: str | None = None,
    chapter_1a_applicable: bool | None = None,
) -> ModuleResult:
    """Run setback pipeline and return a standardized ModuleResult.

    Accepts string values for overlay params (specific_plan, cpio,
    d_limitation, q_condition) so the plan/overlay name is preserved
    in inputs_summary and plain_language_result. The underlying pipeline
    treats any truthy value as "present".

    The result is per-edge — module_payload["edges"] carries one dict per
    EdgeResult. No single governing setback scalar is produced.
    """
    output = _build_setback_output(
        project_inputs=project_inputs,
        raw_zone=raw_zone,
        base_zone=base_zone,
        height_district=height_district,
        specific_plan=specific_plan,
        cpio=cpio,
        d_limitation=d_limitation,
        q_condition=q_condition,
        chapter_1a_applicable=chapter_1a_applicable,
    )
    result = build_setback_result(output)

    coverage = _map_coverage_level(result)
    run_status = _map_run_status(coverage)
    blocking = _map_blocking(result)
    confidence = _map_confidence(result)
    action_posture = _map_action_posture(result, output)

    return ModuleResult(
        module="setback",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=blocking,
        inputs_summary=_build_inputs_summary(
            base_zone=base_zone,
            raw_zone=raw_zone,
            project_inputs=project_inputs,
            specific_plan=specific_plan,
            cpio=cpio,
            d_limitation=d_limitation,
            q_condition=q_condition,
        ),
        interpretation=Interpretation(
            summary=_build_summary_str(result, output),
            plain_language_result=_build_plain_language_result(result, output),
            action_posture=action_posture,
        ),
        provenance=_build_provenance(result),
        module_payload=_build_module_payload(result, output),
    )
