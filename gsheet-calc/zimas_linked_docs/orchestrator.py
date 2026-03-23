"""ZIMAS linked-document orchestrator.

Thin pipeline coordinator. Calls each step in order and collects all issues.
No business logic lives here — each step's module owns its rules.

Pipeline:
    link_detector       → candidates
    doc_classifier      → records
    doc_registry        → registry
    fetch_policy        → fetch_decisions (mutates record.fetch_decision)
    structure_extractor → (mutates record extracted fields and fetch_status)
    confidence          → (mutates record.confidence_state)
    gatekeeper          → interrupt_decisions

Output: ZimasLinkedDocOutput

Usage:
    from zimas_linked_docs.orchestrator import run_zimas_linked_doc_pipeline
    from zimas_linked_docs.models import ZimasLinkedDocInput

    inp = ZimasLinkedDocInput(
        apn="1234-567-890",
        specific_plan="Venice Specific Plan",
        overlay_zones=["Venice CPIO"],
        q_conditions=["Q"],
        d_limitations=[],
    )
    output = run_zimas_linked_doc_pipeline(inp)

To get a standardized ModuleResult envelope:
    from zimas_linked_docs.orchestrator import run_zimas_linked_doc_module
    result = run_zimas_linked_doc_module(inp)
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    ZimasLinkedDocInput,
    ZimasLinkedDocOutput,
    ZimasDocIssue,
    LinkedDocRecord,
    RegistryInterpretation,
    InterruptDecision,
    INPUT_COVERAGE_COMPLETE,
    INPUT_COVERAGE_PARTIAL,
    INPUT_COVERAGE_THIN,
    INPUT_COVERAGE_UNCERTAIN,
    INTERRUPT_NONE,
    INTERRUPT_REFUSE,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_ZI_DOCUMENT,
    DQ_RETRIEVAL_ZI_CORROBORATED,
    DQ_FEASIBILITY_ZI_MEDIATED,
)
from zimas_linked_docs.input_coverage import assess_input_coverage
from zimas_linked_docs.link_detector import detect_linked_docs
from zimas_linked_docs.doc_classifier import classify_candidates
from zimas_linked_docs.doc_registry import build_registry
from zimas_linked_docs.fetch_policy import assign_fetch_decisions
from zimas_linked_docs.structure_extractor import extract_surface_fields
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.gatekeeper import evaluate_interrupts
from zimas_linked_docs.narrowing_context import build_narrowing_context

from models.result_common import (
    ModuleResult,
    RunStatus,
    CoverageLevel,
    ConfidenceLevel,
    ActionPosture,
    Interpretation,
    Provenance,
)


_COVERAGE_DESCRIPTIONS: dict[str, str] = {
    "complete": "complete",
    "partial": "partial (some detection sources absent)",
    "thin": "thin (most detection sources absent)",
    "uncertain": "uncertain (zone string parse failed)",
}

_COVERAGE_MAP: dict[str, CoverageLevel] = {
    INPUT_COVERAGE_COMPLETE: CoverageLevel.COMPLETE,
    INPUT_COVERAGE_PARTIAL: CoverageLevel.PARTIAL,
    INPUT_COVERAGE_THIN: CoverageLevel.THIN,
    INPUT_COVERAGE_UNCERTAIN: CoverageLevel.UNCERTAIN,
}


def _normalize_ord(s: str) -> str:
    """Normalise an ordinance number string for comparison.

    Strips whitespace and converts to uppercase so that "o-186481",
    "O-186481", and " O-186481 " all compare as equal.
    """
    return s.strip().upper()


def _correlate_dq_zi(records: list[LinkedDocRecord]) -> None:
    """Cross-reference D/Q ordinance numbers against fetched ZI records.

    When a ZI document has been fetched and its extracted_ordinance_number
    matches a D/Q record's source_ordinance_number, that is independent
    corroboration of the ordinance's identity from a second ZIMAS-verified
    source (the ZI).  Upgrades ordinance_retrieval_status to
    DQ_RETRIEVAL_ZI_CORROBORATED on the matching D/Q record.

    This is a cross-reference step only — it never changes confidence_state,
    never alters interrupt levels, and never modifies ZI records.

    Mutates records in-place.  Returns nothing (issues are not generated
    here because no error condition exists — corroboration is a best-effort
    signal; absence of corroboration is not a problem).
    """
    # Collect confirmed ordinance numbers from fetched ZI records.
    # Only use extracted_ordinance_number (populated after a successful fetch);
    # source_ordinance_number on ZI records is not set by this pipeline.
    zi_ordinance_numbers: set[str] = set()
    for r in records:
        if r.doc_type == DOC_TYPE_ZI_DOCUMENT and r.extracted_ordinance_number:
            zi_ordinance_numbers.add(_normalize_ord(r.extracted_ordinance_number))

    if not zi_ordinance_numbers:
        return  # nothing to cross-reference against

    for r in records:
        if r.doc_type not in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION):
            continue
        if not r.source_ordinance_number:
            continue
        if _normalize_ord(r.source_ordinance_number) in zi_ordinance_numbers:
            r.ordinance_retrieval_status = DQ_RETRIEVAL_ZI_CORROBORATED
            # ZI corroboration is also the best realistic retrieval path:
            # the fetched ZI may contain the condition text inline, making
            # direct ordinance retrieval unnecessary.
            r.dq_retrieval_feasibility = DQ_FEASIBILITY_ZI_MEDIATED


def _build_interpretation(
    coverage_level: str,
    records_found: int,
) -> RegistryInterpretation:
    """Construct a plain-language registry interpretation.

    Explicitly separates search coverage quality (may we have missed items?)
    from detected record validity (are the items we found correct?).
    """
    may_have_undetected = coverage_level != INPUT_COVERAGE_COMPLETE
    coverage_desc = _COVERAGE_DESCRIPTIONS.get(coverage_level, coverage_level)

    if records_found == 0:
        if not may_have_undetected:
            summary = (
                f"Search coverage was {coverage_desc}. "
                "No linked authority items detected. "
                "Result is plausibly trustworthy."
            )
        else:
            summary = (
                f"Search coverage was {coverage_desc}. "
                "No linked authority items detected, but inputs were insufficient "
                "to complete the search. "
                "This result should NOT be treated as evidence of no linked authority."
            )
    else:
        if not may_have_undetected:
            summary = (
                f"{records_found} linked authority item(s) detected. "
                f"Search coverage was {coverage_desc}. "
                "Result is plausibly complete."
            )
        else:
            summary = (
                f"{records_found} linked authority item(s) detected from "
                "ZIMAS-verified sources and should be acted on. "
                f"Search coverage was {coverage_desc}. "
                "Additional linked authority may exist that was not detected. "
                "Detected records are valid regardless of coverage level."
            )

    return RegistryInterpretation(
        coverage_level=coverage_level,
        may_have_undetected_authority=may_have_undetected,
        detected_records_are_valid=True,
        records_found=records_found,
        summary=summary,
    )


def run_zimas_linked_doc_pipeline(
    inp: ZimasLinkedDocInput,
    fetch_enabled: bool = False,
) -> ZimasLinkedDocOutput:
    """Run the full linked-document pipeline and return the output.

    fetch_enabled=False (default) skips actual HTTP fetching. Set to True
    only when the fetch layer is fully implemented and verified.

    All issues from all steps are collected into output.all_issues.
    No step failure suppresses subsequent steps — the pipeline always runs
    to completion so the caller gets a full picture of what was and was not
    resolved.
    """
    all_issues: list[ZimasDocIssue] = []

    # Step 0: assess input coverage before detection runs.
    # Issues surface first in all_issues so they appear before detection results.
    input_coverage, issues = assess_input_coverage(inp)
    all_issues.extend(issues)

    # Step 1: detect
    candidates, issues = detect_linked_docs(inp)
    all_issues.extend(issues)

    # Step 2: classify
    records, issues = classify_candidates(candidates, apn=inp.apn)
    all_issues.extend(issues)

    # Step 3: registry — receives input_coverage so zero-record warnings are
    # calibrated to whether the search was actually thorough
    registry, issues = build_registry(records, apn=inp.apn, input_coverage=input_coverage)
    all_issues.extend(issues)

    # Step 4: fetch policy (mutates record.fetch_decision)
    fetch_decisions, issues = assign_fetch_decisions(records)
    all_issues.extend(issues)

    # Step 5: structure extraction (mutates record fields; skipped when fetch_enabled=False)
    # Build narrowing context from available inputs so CPIO extraction can use
    # source-ranked, conflict-aware subarea/overlay context instead of a bare string.
    narrowing_ctx = build_narrowing_context(inp)
    records, issues = extract_surface_fields(
        records,
        _fetch_enabled=fetch_enabled,
        _narrowing_context=narrowing_ctx,
    )
    all_issues.extend(issues)

    # Step 6: confidence state assignment (mutates record.confidence_state)
    records, issues = assign_confidence_states(records)
    all_issues.extend(issues)

    # Rebuild registry summary counts now that confidence states are final
    from zimas_linked_docs.models import CONF_DETECTED_NOT_INTERPRETED, CONF_DETECTED_URL_UNVERIFIED, CONF_REFUSE_TO_DECIDE, POSTURE_CONFIDENCE_INTERRUPTER_ONLY
    _UNRESOLVED = {CONF_DETECTED_NOT_INTERPRETED, CONF_DETECTED_URL_UNVERIFIED, CONF_REFUSE_TO_DECIDE}
    registry.unresolved_count = sum(1 for r in records if r.confidence_state in _UNRESOLVED)
    registry.interrupt_doc_count = sum(1 for r in records if r.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY)

    # Step 6b: D/Q × ZI corroboration — cross-reference D/Q ordinance numbers
    # against fetched ZI records.  Upgrades ordinance_retrieval_status to
    # DQ_RETRIEVAL_ZI_CORROBORATED when there is an independent match.
    # Must run after confidence (which sets ordinance_retrieval_status) and
    # before gatekeeper (so rigor summaries can reflect corroboration).
    # This step is a no-op when no ZI records have been fetched.
    _correlate_dq_zi(records)

    # Step 7: gatekeeper
    interrupt_decisions, issues = evaluate_interrupts(
        registry, topics=inp.topics_to_evaluate
    )
    all_issues.extend(issues)

    interpretation = _build_interpretation(
        coverage_level=input_coverage,
        records_found=len(records),
    )

    return ZimasLinkedDocOutput(
        registry=registry,
        interrupt_decisions=interrupt_decisions,
        candidates_detected=len(candidates),
        records_classified=len(records),
        fetch_decisions=fetch_decisions,
        all_issues=all_issues,
        registry_input_coverage=input_coverage,
        interpretation=interpretation,
    )


# ── ModuleResult adapter ──────────────────────────────────────────────────────


def _map_coverage_level(raw: str) -> CoverageLevel:
    level = _COVERAGE_MAP.get(raw)
    if level is None:
        # Unexpected value — surface as UNCERTAIN rather than swallow silently.
        return CoverageLevel.UNCERTAIN
    return level


def _map_run_status(coverage: CoverageLevel, registry_confidence: str) -> RunStatus:
    if coverage == CoverageLevel.COMPLETE and registry_confidence == "clean":
        return RunStatus.OK
    return RunStatus.PARTIAL


def _map_confidence(
    coverage: CoverageLevel,
    registry_confidence: str,
    interrupt_decisions: list[InterruptDecision],
) -> ConfidenceLevel:
    # Uncertain coverage: zone parse failed; registry cannot be trusted.
    if coverage == CoverageLevel.UNCERTAIN:
        return ConfidenceLevel.UNRESOLVED

    # Any refuse_to_decide interrupt: irresolvable state.
    if any(d.interrupt_level == INTERRUPT_REFUSE for d in interrupt_decisions):
        return ConfidenceLevel.UNRESOLVED

    if registry_confidence == "has_interrupters":
        return ConfidenceLevel.LOW

    if registry_confidence == "provisional":
        return ConfidenceLevel.MEDIUM

    # Clean registry.
    if coverage == CoverageLevel.COMPLETE:
        return ConfidenceLevel.HIGH

    # Clean registry but search was not thorough — gaps may exist.
    return ConfidenceLevel.MEDIUM


def _map_action_posture(
    coverage: CoverageLevel,
    interrupt_decisions: list[InterruptDecision],
) -> ActionPosture:
    # Blocking interrupt (unresolved or refuse_to_decide) takes priority.
    if any(d.blocking for d in interrupt_decisions):
        return ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED

    # Thin or uncertain coverage with no blocking interrupt.
    if coverage in (CoverageLevel.UNCERTAIN, CoverageLevel.THIN):
        return ActionPosture.INSUFFICIENT_FOR_PERMIT_USE

    # Partial coverage, or any non-none interrupt level present.
    if coverage == CoverageLevel.PARTIAL or any(
        d.interrupt_level != INTERRUPT_NONE for d in interrupt_decisions
    ):
        return ActionPosture.ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS

    # Complete coverage, clean registry, no interrupts.
    return ActionPosture.CAN_RELY_WITH_REVIEW


def _build_provenance(inp: ZimasLinkedDocInput) -> Provenance:
    authoritative: list[str] = []
    non_authoritative: list[str] = []

    if any([inp.specific_plan, inp.overlay_zones, inp.q_conditions, inp.d_limitations]):
        authoritative.append("zimas_site_fields")
    if inp.raw_zimas_identify.get("results"):
        authoritative.append("zimas_identify_layers")
    if inp.zoning_parse_confidence is not None:
        authoritative.append("zone_string_parser")
    if inp.raw_text_fragments:
        non_authoritative.append("raw_text_fragments")

    source_types = list(authoritative) + non_authoritative

    return Provenance(
        source_types=source_types,
        authoritative_sources_used=authoritative,
        non_authoritative_sources_used=non_authoritative,
    )


def _to_module_result(
    inp: ZimasLinkedDocInput,
    output: ZimasLinkedDocOutput,
) -> ModuleResult:
    coverage = _map_coverage_level(output.registry_input_coverage)
    run_status = _map_run_status(coverage, output.registry.registry_confidence)
    confidence = _map_confidence(coverage, output.registry.registry_confidence, output.interrupt_decisions)
    is_blocking = any(d.blocking for d in output.interrupt_decisions)
    action_posture = _map_action_posture(coverage, output.interrupt_decisions)

    n_blocking_topics = sum(1 for d in output.interrupt_decisions if d.blocking)
    n_topics = len(output.interrupt_decisions)
    summary = (
        f"zimas_linked_docs: {output.records_classified} record(s) classified, "
        f"coverage={output.registry_input_coverage}, "
        f"registry={output.registry.registry_confidence}, "
        f"{n_blocking_topics}/{n_topics} topics blocking"
    )

    return ModuleResult(
        module="zimas_linked_docs",
        run_status=run_status,
        coverage_level=coverage,
        confidence=confidence,
        blocking=is_blocking,
        inputs_summary={
            "apn": inp.apn,
            "specific_plan": inp.specific_plan,
            "overlay_zones": inp.overlay_zones,
            "q_conditions": inp.q_conditions,
            "d_limitations": inp.d_limitations,
            "has_raw_zimas_identify": bool(inp.raw_zimas_identify.get("results")),
            "zoning_parse_confidence": inp.zoning_parse_confidence,
            "topics_evaluated": inp.topics_to_evaluate,
        },
        interpretation=Interpretation(
            summary=summary,
            plain_language_result=output.interpretation.summary,
            action_posture=action_posture,
        ),
        provenance=_build_provenance(inp),
        module_payload=output.model_dump(),
    )


def run_zimas_linked_doc_module(
    inp: ZimasLinkedDocInput,
    fetch_enabled: bool = False,
) -> ModuleResult:
    """Run the linked-document pipeline and return a standardized ModuleResult.

    The full ZimasLinkedDocOutput is preserved in result.module_payload.
    The internal pipeline is unchanged — this function is a thin wrapper.
    """
    output = run_zimas_linked_doc_pipeline(inp, fetch_enabled=fetch_enabled)
    return _to_module_result(inp, output)
