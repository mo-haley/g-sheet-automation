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
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    ZimasLinkedDocInput,
    ZimasLinkedDocOutput,
    ZimasDocIssue,
)
from zimas_linked_docs.input_coverage import assess_input_coverage
from zimas_linked_docs.link_detector import detect_linked_docs
from zimas_linked_docs.doc_classifier import classify_candidates
from zimas_linked_docs.doc_registry import build_registry
from zimas_linked_docs.fetch_policy import assign_fetch_decisions
from zimas_linked_docs.structure_extractor import extract_surface_fields
from zimas_linked_docs.confidence import assign_confidence_states
from zimas_linked_docs.gatekeeper import evaluate_interrupts


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
    records, issues = extract_surface_fields(records, _fetch_enabled=fetch_enabled)
    all_issues.extend(issues)

    # Step 6: confidence state assignment (mutates record.confidence_state)
    records, issues = assign_confidence_states(records)
    all_issues.extend(issues)

    # Rebuild registry summary counts now that confidence states are final
    from zimas_linked_docs.models import CONF_DETECTED_NOT_INTERPRETED, CONF_DETECTED_URL_UNVERIFIED, CONF_REFUSE_TO_DECIDE, POSTURE_CONFIDENCE_INTERRUPTER_ONLY
    _UNRESOLVED = {CONF_DETECTED_NOT_INTERPRETED, CONF_DETECTED_URL_UNVERIFIED, CONF_REFUSE_TO_DECIDE}
    registry.unresolved_count = sum(1 for r in records if r.confidence_state in _UNRESOLVED)
    registry.interrupt_doc_count = sum(1 for r in records if r.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY)

    # Step 7: gatekeeper
    interrupt_decisions, issues = evaluate_interrupts(
        registry, topics=inp.topics_to_evaluate
    )
    all_issues.extend(issues)

    return ZimasLinkedDocOutput(
        registry=registry,
        interrupt_decisions=interrupt_decisions,
        candidates_detected=len(candidates),
        records_classified=len(records),
        fetch_decisions=fetch_decisions,
        all_issues=all_issues,
        registry_input_coverage=input_coverage,
    )
