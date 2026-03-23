"""Debug trace for address-only snapshot logic.

Produces a compact, non-user-facing summary explaining *why* each
view-model decision was made.  Use for calibration during development.

Usage:
    from web.snapshot_debug import build_debug_trace

    trace = build_debug_trace(site, app_result)
    # trace is a plain dict — log it, render it, or return as JSON

Enable in the web app by passing ?debug=1 to /run-address (only when
Flask app.debug is True).
"""

from __future__ import annotations

from models.result_common import (
    ActionPosture,
    AppResult,
    CoverageLevel,
    ConfidenceLevel,
    RunStatus,
)
from models.site import Site
from web.snapshot_view import (
    CoverageLabel,
    _by_name,
    _collect_authority_flags,
    _derive_coverage_label,
    _module_display,
    build_snapshot_view,
)


def build_debug_trace(site: Site, app: AppResult) -> dict:
    """Return a compact dict explaining every view-model decision."""
    vm = build_snapshot_view(site, app)
    trace = {
        "coverage": _trace_coverage(site, app),
        "signals": _trace_signals(site, app, vm),
        "scenarios": _trace_scenarios(site, app, vm),
        "best_next_inputs": _trace_best_next(site, app, vm),
        "caveats": _trace_caveats(site, app, vm),
    }
    ed1_trace = _trace_ed1(app, vm)
    if ed1_trace:
        trace["ed1"] = ed1_trace
    return trace


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _trace_coverage(site: Site, app: AppResult) -> dict:
    results = app.module_results
    total = len(results)
    levels = [mr.coverage_level.value for mr in results]
    blocked_or_error = sum(
        1 for mr in results
        if mr.run_status in (RunStatus.BLOCKED, RunStatus.ERROR)
    )
    thin_or_worse = sum(
        1 for mr in results
        if mr.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE)
    )
    authority_modules = [
        mr.module for mr in results
        if mr.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
    ]

    label = _derive_coverage_label(app)

    # Explain which branch was taken
    if blocked_or_error > total / 2 or thin_or_worse == total:
        reason = f"Interrupted: blocked_or_error={blocked_or_error}/{total}, thin_or_worse={thin_or_worse}/{total}"
    elif all(lv == "complete" for lv in levels):
        reason = "High: all modules complete"
    elif thin_or_worse >= 3:
        reason = f"Thin: {thin_or_worse} modules thin-or-worse (>=3 threshold)"
    else:
        reason = f"Moderate: {thin_or_worse} thin, not all complete"

    return {
        "label": label.value,
        "reason": reason,
        "per_module": {
            mr.module: {
                "coverage": mr.coverage_level.value,
                "status": mr.run_status.value,
                "confidence": mr.confidence.value,
                "posture": mr.interpretation.action_posture.value,
            }
            for mr in results
        },
        "authority_flags": authority_modules,
        "counts": {
            "total": total,
            "blocked_or_error": blocked_or_error,
            "thin_or_worse": thin_or_worse,
        },
    }


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

_SLOT_NAMES = ["opportunity", "uncertainty", "constraint", "comparison", "next_step"]


def _trace_signals(site: Site, app: AppResult, vm) -> dict:
    """Explain which slots were filled and what condition triggered each."""
    density = _by_name(app.module_results, "density")
    setback = _by_name(app.module_results, "setback")
    zimas = _by_name(app.module_results, "zimas_linked_docs")
    has_overlays = bool(
        site.overlay_zones or site.q_conditions
        or site.d_limitations or site.specific_plan
    )
    thin_modules = [
        mr.module for mr in app.module_results
        if mr.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN)
    ]

    slots: dict[str, dict] = {}

    # Opportunity
    if not has_overlays and all(
        mr.coverage_level == CoverageLevel.COMPLETE for mr in app.module_results
    ):
        slots["opportunity"] = {"filled": True, "trigger": "all_complete_no_overlays"}
    elif site.toc_tier or site.ab2097_area:
        slots["opportunity"] = {"filled": True, "trigger": f"toc_tier={site.toc_tier} ab2097={site.ab2097_area}"}
    elif density and density.coverage_level == CoverageLevel.COMPLETE:
        slots["opportunity"] = {"filled": True, "trigger": "density_complete"}
    else:
        slots["opportunity"] = {"filled": False, "trigger": "no_match"}

    # Uncertainty
    if density and density.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        slots["uncertainty"] = {"filled": True, "trigger": f"density_{density.coverage_level.value}"}
    elif setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        slots["uncertainty"] = {"filled": True, "trigger": f"setback_{setback.coverage_level.value}"}
    elif len(thin_modules) >= 2:
        slots["uncertainty"] = {"filled": True, "trigger": f"multi_thin: {thin_modules[:2]}"}
    else:
        slots["uncertainty"] = {"filled": False, "trigger": "no_match"}

    # Constraint
    if zimas and zimas.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED:
        slots["constraint"] = {"filled": True, "trigger": "zimas_authority_required"}
    elif site.specific_plan:
        slots["constraint"] = {"filled": True, "trigger": f"specific_plan={site.specific_plan}"}
    elif has_overlays:
        slots["constraint"] = {"filled": True, "trigger": "has_overlays"}
    else:
        slots["constraint"] = {"filled": False, "trigger": "no_match"}

    # Comparison
    candidates = (density.module_payload.get("candidate_routes") or []) if density else []
    if len(candidates) > 1:
        slots["comparison"] = {"filled": True, "trigger": f"{len(candidates)}_candidate_routes"}
    elif site.toc_tier and density and density.coverage_level != CoverageLevel.UNCERTAIN:
        slots["comparison"] = {"filled": True, "trigger": "toc_vs_base"}
    else:
        slots["comparison"] = {"filled": False, "trigger": "no_match"}

    # Next step
    manual = [mr.module for mr in app.module_results if mr.requires_manual_input()]
    if manual:
        slots["next_step"] = {"filled": True, "trigger": f"manual_input_modules={manual}"}
    elif thin_modules:
        slots["next_step"] = {"filled": True, "trigger": "thin_modules_present"}
    else:
        slots["next_step"] = {"filled": False, "trigger": "no_match"}

    filled_count = sum(1 for s in slots.values() if s["filled"])

    return {
        "slots": slots,
        "filled_count": filled_count,
        "total_slots": len(_SLOT_NAMES),
        "output_texts": [s.text for s in vm.signals],
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _trace_scenarios(site: Site, app: AppResult, vm) -> dict:
    density = _by_name(app.module_results, "density")
    candidates = (density.module_payload.get("candidate_routes") or []) if density else []
    has_state_db = any(
        isinstance(c, dict) and c.get("lane") == "state_db"
        for c in candidates
    ) if candidates else False

    checks = {
        "base_zoning": {"included": True, "trigger": "always"},
        "ab2097": {
            "included": bool(site.ab2097_area),
            "trigger": f"ab2097_area={site.ab2097_area}",
        },
        "transit_proximity": {
            "included": (
                not site.ab2097_area
                and site.nearest_transit_stop_distance_ft is not None
                and site.nearest_transit_stop_distance_ft <= 2640
            ),
            "trigger": f"transit_ft={site.nearest_transit_stop_distance_ft}",
        },
        "toc": {
            "included": bool(site.toc_tier),
            "trigger": f"toc_tier={site.toc_tier}",
        },
        "state_density_bonus": {
            "included": has_state_db,
            "trigger": f"state_db_in_candidates={has_state_db} (candidates={len(candidates)})",
        },
    }

    return {
        "checks": checks,
        "rows_included": [row.scenario for row in vm.scenarios],
        "row_count": len(vm.scenarios),
    }


# ---------------------------------------------------------------------------
# Best Next Inputs
# ---------------------------------------------------------------------------

def _trace_best_next(site: Site, app: AppResult, vm) -> dict:
    density = _by_name(app.module_results, "density")
    setback = _by_name(app.module_results, "setback")
    candidates = (density.module_payload.get("candidate_routes") or []) if density else []

    ranking_reasons = []

    # Reason 1
    if density or _by_name(app.module_results, "parking"):
        ranking_reasons.append({
            "input": "unit_count_and_mix",
            "reason": "density or parking module present; highest cross-module leverage",
        })

    # Reason 2
    if setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        ranking_reasons.append({
            "input": "lot_edge_geometry",
            "reason": f"setback coverage={setback.coverage_level.value}; geometry moves it to preliminary",
        })

    # Reason 3
    if site.toc_tier:
        ranking_reasons.append({
            "input": "affordability_strategy",
            "reason": f"toc_tier={site.toc_tier}; determines incentive applicability",
        })
    elif len(candidates) > 1:
        ranking_reasons.append({
            "input": "entitlement_path_selection",
            "reason": f"{len(candidates)} candidate routes; selecting one firms up density+parking",
        })

    return {
        "ranking_reasons": ranking_reasons,
        "selected": [inp.name for inp in vm.best_next_inputs],
        "count": len(vm.best_next_inputs),
    }


# ---------------------------------------------------------------------------
# Caveats
# ---------------------------------------------------------------------------

def _trace_caveats(site: Site, app: AppResult, vm) -> dict:
    setback = _by_name(app.module_results, "setback")
    zimas = _by_name(app.module_results, "zimas_linked_docs")

    selection_log: list[dict] = []

    # Each caveat and its trigger
    if setback and setback.coverage_level in (CoverageLevel.THIN, CoverageLevel.UNCERTAIN):
        selection_log.append({
            "caveat": "setback_geometry",
            "trigger": f"setback.coverage={setback.coverage_level.value}",
        })

    selection_log.append({
        "caveat": "no_project_program",
        "trigger": "always (address-only mode)",
    })

    selection_log.append({
        "caveat": "no_affordability_path",
        "trigger": "always (address-only mode)",
    })

    if site.specific_plan or site.overlay_zones:
        selection_log.append({
            "caveat": "dedication_confirmation",
            "trigger": f"specific_plan={site.specific_plan} overlays={len(site.overlay_zones or [])}",
        })

    if zimas and zimas.interpretation.action_posture in (
        ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED,
        ActionPosture.INSUFFICIENT_FOR_PERMIT_USE,
    ):
        selection_log.append({
            "caveat": "authority_linkage_partial",
            "trigger": f"zimas.posture={zimas.interpretation.action_posture.value}",
        })

    blocking_issues = [
        {"module": mr.module, "message": iss.message}
        for mr in app.module_results
        for iss in mr.issues
        if iss.blocking
    ]
    if blocking_issues:
        selection_log.append({
            "caveat": "blocking_issues",
            "trigger": f"{len(blocking_issues)} blocking issue(s)",
            "issues": blocking_issues[:3],
        })

    return {
        "selection_log": selection_log,
        "output_count": len(vm.caveats),
        "max_allowed": 7,
    }


# ---------------------------------------------------------------------------
# ED1
# ---------------------------------------------------------------------------

def _trace_ed1(app: AppResult, vm) -> dict | None:
    """Trace ED1 screening section construction."""
    ed1_mr = _by_name(app.module_results, "ed1")
    if ed1_mr is None:
        return None

    payload = ed1_mr.module_payload or {}
    section = vm.ed1_screening

    return {
        "module_present": True,
        "status": payload.get("status"),
        "confidence": payload.get("confidence"),
        "blocker_count": len(payload.get("blockers", [])),
        "missing_count": len(payload.get("missing_inputs", [])),
        "obligation_count": len(payload.get("obligations", [])),
        "section_built": section is not None,
        "status_tone": section.status_tone if section else None,
    }
