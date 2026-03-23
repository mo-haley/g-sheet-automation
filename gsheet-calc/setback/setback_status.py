"""Setback status assembly (Step 5 of 6 — penultimate module).

Accepts the full SetbackOutput (all upstream results assembled by the
orchestrator) and produces the final SetbackResult:

  - overall_status          — worst-case across all edge results, with
                              authority-level caps applied
  - authority_chain_summary — ordered narrative of zone resolution
  - sources_checked         — LAMC sections / tables successfully applied
  - sources_flagged_not_interpreted — overlays detected but not interpreted
  - sources_not_checked     — sources known to exist but deferred this sprint
  - manual_review_reasons   — aggregated from all edge results
  - inherited_yard_family   — governing family when resolved; None if split

═══════════════════════════════════════════════
STATUS RULES
═══════════════════════════════════════════════

Status cascade (worst → best):
  unresolved > split_condition > provisional > overridden > confirmed

Per-edge status is set by setback_edge_calc.py. Overall status rules:

  1. Overall = worst per-edge status.
  2. Any authority_interrupter present → overall cannot be "confirmed"
     (caps to "provisional" at best).
  3. authority_result.confidence == "unresolved" → overall at least "unresolved".
  4. No edges produced → "unresolved".
  5. Early exit → always "unresolved".

These rules are applied without modifying any EdgeResult — edge-level status
values remain as set by edge_calc.

═══════════════════════════════════════════════
SOURCES LOGIC
═══════════════════════════════════════════════

sources_checked:    LAMC zone table + governing_sections actually applied.
sources_flagged:    authority_interrupters (detected, not interpreted).
sources_not_checked: items intentionally deferred this sprint:
                    - height_district (deferred; may affect taller buildings)
                    - RAS ground-floor commercial formula (not implemented)
"""

from __future__ import annotations

from setback.models import (
    SetbackAuthorityResult,
    SetbackIssue,
    SetbackOutput,
    SetbackResult,
    YardFamilyResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Status ordering
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_ORDER: dict[str, int] = {
    "confirmed": 0,
    "overridden": 1,
    "provisional": 2,
    "split_condition": 3,
    "unresolved": 4,
}


def _worst_status(*statuses: str) -> str:
    """Return the most pessimistic status from a collection of status strings."""
    return max(statuses, key=lambda s: _STATUS_ORDER.get(s, 4))


# ─────────────────────────────────────────────────────────────────────────────
# Authority chain summary
# ─────────────────────────────────────────────────────────────────────────────

def _build_authority_chain_summary(
    authority_result: SetbackAuthorityResult,
    yard_family_result: YardFamilyResult | None,
) -> list[str]:
    """Build an ordered narrative of zone resolution for human review."""
    lines: list[str] = []

    # ── Zone / code family ───────────────────────────────────────────────────
    if authority_result.code_family:
        lines.append(f"Zone resolved — code_family: {authority_result.code_family}")
    else:
        lines.append("Zone resolution: FAILED — code_family not determined")

    # ── Baseline yard family ─────────────────────────────────────────────────
    if authority_result.baseline_yard_family:
        lines.append(
            f"Baseline yard family: {authority_result.baseline_yard_family} "
            "(zone-table lookup; does not account for overlays)"
        )
    else:
        lines.append("Baseline yard family: not resolved")

    # ── Governing yard family ────────────────────────────────────────────────
    if authority_result.governing_yard_family:
        lines.append(
            f"Governing yard family: {authority_result.governing_yard_family} "
            "(no unresolved authority interrupters present)"
        )
    elif authority_result.cm_split:
        lines.append(
            "Governing yard family: SPLIT (CM zone) — "
            "R3 or R4 yards apply depending on use type; "
            "cannot be auto-selected. Confirm use type before applying yard rules."
        )
    elif authority_result.ras_split:
        lines.append(
            "Governing yard family: SPLIT (RAS zone) — "
            "residential-above portion uses R3/R4 yards; "
            "ground-floor commercial yard not resolved this sprint."
        )
    elif authority_result.authority_interrupters:
        sources = ", ".join(
            i.source for i in authority_result.authority_interrupters
        )
        lines.append(
            f"Governing yard family: NOT SET — "
            f"authority interrupters present ({sources}). "
            "Baseline is available but not treated as governing."
        )
    else:
        lines.append("Governing yard family: not resolved")

    # ── Authority interrupters (detail) ──────────────────────────────────────
    for interrupter in authority_result.authority_interrupters:
        lines.append(
            f"  [{interrupter.source}] {interrupter.reason} "
            f"(status: {interrupter.status})"
        )

    # ── Governing LAMC sections ──────────────────────────────────────────────
    if authority_result.governing_sections:
        lines.append(
            "Governing LAMC sections: "
            + ", ".join(authority_result.governing_sections)
        )

    # ── Chapter 1A ───────────────────────────────────────────────────────────
    if authority_result.chapter_1a_applicable is True:
        lines.append("Chapter 1A: applicable (density bonus / Affordable Housing Incentives)")
    elif authority_result.chapter_1a_applicable is False:
        lines.append("Chapter 1A: not applicable to this project")
    else:
        lines.append("Chapter 1A: applicability not determined — verify before finalizing")

    # ── Yard formula status ──────────────────────────────────────────────────
    if yard_family_result is not None:
        lines.append(f"Yard formula status: {yard_family_result.status}")
        if yard_family_result.cm_options:
            for opt in yard_family_result.cm_options:
                lines.append(
                    f"  CM candidate: {opt.use_type_label} → "
                    f"{opt.inherited_family} yards ({opt.governing_section})"
                )

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Sources
# ─────────────────────────────────────────────────────────────────────────────

_INTERRUPTER_LABELS: dict[str, str] = {
    "specific_plan":  "Specific Plan",
    "cpio":           "Community Plan Implementation Overlay (CPIO)",
    "d_limitation":   "D Limitation",
    "q_condition":    "Q Condition",
}


def _build_sources(
    authority_result: SetbackAuthorityResult,
    yard_family_result: YardFamilyResult | None,
) -> tuple[list[str], list[str], list[str]]:
    """Build sources_checked / sources_flagged_not_interpreted / sources_not_checked.

    Returns:
        (checked, flagged_not_interpreted, not_checked)
    """
    checked: list[str] = []
    flagged: list[str] = []
    not_checked: list[str] = []

    # LAMC zone table
    if authority_result.code_family:
        checked.append(
            f"LA City LAMC zone table (code_family: {authority_result.code_family})"
        )

    # Governing LAMC sections applied
    for section in authority_result.governing_sections:
        checked.append(section)

    # Authority interrupters → flagged
    for interrupter in authority_result.authority_interrupters:
        label = _INTERRUPTER_LABELS.get(interrupter.source, interrupter.source)
        flagged.append(
            f"{label}: {interrupter.reason} "
            f"(flagged, not interpreted — status: {interrupter.status})"
        )

    # Deferred sources: height_district
    not_checked.append(
        "height_district — setback effects of height district classification "
        "are deferred to a future sprint; may affect yard requirements for "
        "buildings in taller height districts. Verify before permit use."
    )

    # Deferred sources: RAS ground-floor commercial
    if (
        authority_result.ras_split
        and yard_family_result is not None
        and yard_family_result.ras_ground_floor_formula is None
    ):
        not_checked.append(
            "RAS ground-floor commercial yard formula — not implemented this sprint. "
            "The residential-above formula was applied; the ground-floor commercial "
            "yard requirement must be determined and applied separately."
        )

    return checked, flagged, not_checked


# ─────────────────────────────────────────────────────────────────────────────
# Manual review aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_manual_review(output: SetbackOutput) -> list[str]:
    """Collect and deduplicate all manual_review_reasons from edge results.

    Preserves first-seen order. Does not de-duplicate across semantically
    similar but textually different strings — callers are responsible for
    consistent reason text.
    """
    seen: set[str] = set()
    reasons: list[str] = []

    for edge_result in output.edge_results:
        for reason in edge_result.manual_review_reasons:
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)

    return reasons


# ─────────────────────────────────────────────────────────────────────────────
# Issue collection
# ─────────────────────────────────────────────────────────────────────────────

def _collect_all_issues(output: SetbackOutput) -> list[SetbackIssue]:
    """Gather all SetbackIssue objects from every stage of the pipeline."""
    issues: list[SetbackIssue] = []
    issues.extend(output.authority_result.issues)
    if output.yard_family_result is not None:
        issues.extend(output.yard_family_result.issues)
    for edge_result in output.edge_results:
        issues.extend(edge_result.issues)
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Overall status
# ─────────────────────────────────────────────────────────────────────────────

def _compute_overall_status(output: SetbackOutput) -> str:
    """Determine overall project setback status.

    Rules (applied in order):
      1. Early exit or no edges → "unresolved"
      2. Start with worst per-edge status.
      3. authority_result.confidence == "unresolved" → at least "unresolved"
      4. Any authority_interrupter present → cannot be "confirmed";
         cap to "provisional" if currently "confirmed"
    """
    if output.authority_result.early_exit.triggered:
        return "unresolved"

    if not output.edge_results:
        return "unresolved"

    status = _worst_status(*[r.status for r in output.edge_results])

    # Authority confidence cap
    if output.authority_result.confidence == "unresolved":
        status = _worst_status(status, "unresolved")

    # Authority interrupter cap: interrupters mean governing_yard_family is None,
    # so the result cannot be "confirmed" at the project level.
    if output.authority_result.authority_interrupters:
        if status == "confirmed":
            status = "provisional"

    return status


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def build_setback_result(output: SetbackOutput) -> SetbackResult:
    """Assemble the final SetbackResult from all upstream pipeline results.

    Args:
        output: SetbackOutput containing all upstream results assembled by
                the orchestrator (inputs, authority_result, yard_family_result,
                classified_edges, edge_results).

    Returns:
        SetbackResult — final output ready for the caller.
        Edge-level values are taken directly from output.edge_results;
        this module does not modify or re-compute any yard values.

    Does NOT:
        - modify any EdgeResult.status value
        - re-run any yard formula
        - resolve any split condition or authority interrupter
    """
    authority_result = output.authority_result

    # ── Early exit fast path ─────────────────────────────────────────────────
    if authority_result.early_exit.triggered:
        return SetbackResult(
            edges=list(output.edge_results),
            overall_status="unresolved",
            authority_chain_summary=[
                f"Early exit triggered: {authority_result.early_exit.reason}"
            ],
            code_family=authority_result.code_family,
            inherited_yard_family=None,
            sources_checked=[],
            sources_flagged_not_interpreted=[],
            sources_not_checked=[],
            manual_review_reasons=[
                f"Early exit: {authority_result.early_exit.reason}. "
                "No setback values were computed. "
                "Resolve the early-exit condition before re-running this module."
            ],
            early_exit=authority_result.early_exit,
            all_issues=_collect_all_issues(output),
        )

    yard_family_result = output.yard_family_result

    overall_status = _compute_overall_status(output)
    chain_summary = _build_authority_chain_summary(authority_result, yard_family_result)
    sources_checked, sources_flagged, sources_not_checked = _build_sources(
        authority_result, yard_family_result
    )
    manual_review_reasons = _aggregate_manual_review(output)

    # inherited_yard_family: governing when resolved; None when any split/interrupter
    inherited_yard_family = authority_result.governing_yard_family

    return SetbackResult(
        edges=list(output.edge_results),
        overall_status=overall_status,
        authority_chain_summary=chain_summary,
        code_family=authority_result.code_family,
        inherited_yard_family=inherited_yard_family,
        sources_checked=sources_checked,
        sources_flagged_not_interpreted=sources_flagged,
        sources_not_checked=sources_not_checked,
        manual_review_reasons=manual_review_reasons,
        early_exit=authority_result.early_exit,
        all_issues=_collect_all_issues(output),
    )
