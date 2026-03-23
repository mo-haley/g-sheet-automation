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
"""

from __future__ import annotations

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

    # ── Assemble pipeline state ──────────────────────────────────────────────
    output = SetbackOutput(
        inputs=project_inputs,
        authority_result=authority_result,
        yard_family_result=yard_family_result,
        classified_edges=classified_edges,
        edge_results=edge_results,
    )

    # ── Step 5: Status / result assembly ────────────────────────────────────
    return build_setback_result(output)
