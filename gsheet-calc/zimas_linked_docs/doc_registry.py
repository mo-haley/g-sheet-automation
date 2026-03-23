"""ZIMAS linked-document registry builder.

Takes classified LinkedDocRecord list and produces a LinkedDocRegistry.
Computes summary flags and aggregate confidence for the parcel.

The registry is the canonical store for a single parcel run. Downstream steps
(fetch_policy, confidence, gatekeeper) consume the registry, not raw records.

Registry confidence levels:
    "clean"            — no records detected (rare; verify input was populated)
    "provisional"      — only q/d conditions or minor overlays (no specific plan, no CPIO)
    "has_interrupters" — specific plan, CPIO, case doc, or refuse_to_decide items present
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    LinkedDocRecord,
    LinkedDocRegistry,
    ZimasDocIssue,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_CASE_DOCUMENT,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_DETECTED_URL_UNVERIFIED,
    CONF_REFUSE_TO_DECIDE,
    INPUT_COVERAGE_COMPLETE,
    INPUT_COVERAGE_PARTIAL,
)

_UNRESOLVED_STATES = {
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_DETECTED_URL_UNVERIFIED,
    CONF_REFUSE_TO_DECIDE,
}

_STRONG_INTERRUPTERS = {
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_CASE_DOCUMENT,
}


def build_registry(
    records: list[LinkedDocRecord],
    apn: str | None = None,
    input_coverage: str = INPUT_COVERAGE_PARTIAL,
) -> tuple[LinkedDocRegistry, list[ZimasDocIssue]]:
    """Build the canonical linked-document registry for a parcel.

    input_coverage should be the result of assess_input_coverage(). It is
    set on the registry and used to calibrate the severity of zero-record
    warnings — a "clean" result from thin inputs is not evidence of no
    linked authority.

    Returns (registry, issues).
    """
    issues: list[ZimasDocIssue] = []

    specific_plan_detected = any(r.doc_type == DOC_TYPE_SPECIFIC_PLAN for r in records)
    cpio_detected = any(r.doc_type == DOC_TYPE_OVERLAY_CPIO for r in records)
    q_condition_detected = any(r.doc_type == DOC_TYPE_Q_CONDITION for r in records)
    d_limitation_detected = any(r.doc_type == DOC_TYPE_D_LIMITATION for r in records)
    zi_document_detected = any(r.doc_type == DOC_TYPE_ZI_DOCUMENT for r in records)
    case_document_detected = any(r.doc_type == DOC_TYPE_CASE_DOCUMENT for r in records)

    unresolved_count = sum(
        1 for r in records if r.confidence_state in _UNRESOLVED_STATES
    )
    interrupt_doc_count = sum(
        1 for r in records if r.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY
    )

    # Aggregate registry confidence
    if not records:
        registry_confidence = "clean"
        # Severity depends on input coverage. A "clean" registry from thin or
        # uncertain inputs is not informative — it may simply reflect that the
        # search was inadequate. Only treat "clean" as meaningful when coverage
        # is "complete".
        if input_coverage == INPUT_COVERAGE_COMPLETE:
            zero_severity = "info"
            zero_message = (
                "Registry built with zero records. "
                "Input coverage was complete — this result is plausibly trustworthy, "
                "but manual spot-check is still recommended."
            )
            zero_action = ""
        else:
            zero_severity = "warning"
            zero_message = (
                f"Registry built with zero records, but input coverage is "
                f"'{input_coverage}'. "
                "Sparse results here may reflect weak inputs, not genuine absence "
                "of linked authority. Do not treat a clean registry as evidence "
                "that no linked authority governs this parcel."
            )
            zero_action = (
                "Review input_coverage issues for what detection sources were absent. "
                "Re-run with more complete inputs before drawing conclusions."
            )
        issues.append(
            ZimasDocIssue(
                step="doc_registry",
                field="records",
                severity=zero_severity,
                message=zero_message,
                action_required=zero_action,
                confidence_impact="none",
            )
        )
    elif any(r.doc_type in _STRONG_INTERRUPTERS for r in records):
        registry_confidence = "has_interrupters"
    elif q_condition_detected or d_limitation_detected:
        registry_confidence = "provisional"
    else:
        registry_confidence = "provisional"

    # Warn on specific plan without subarea info
    for r in records:
        if r.doc_type == DOC_TYPE_SPECIFIC_PLAN:
            if not any("subarea" in v.lower() for v in r.raw_values):
                issues.append(
                    ZimasDocIssue(
                        step="doc_registry",
                        field=r.record_id,
                        severity="warning",
                        message=(
                            f"Specific plan '{r.doc_label}' detected but no subarea information found. "
                            "Specific plan subareas govern separate dimensional standards. "
                            "Subarea placement must be confirmed manually."
                        ),
                        action_required="Confirm subarea from specific plan map/text.",
                        confidence_impact="degrades_to_unresolved",
                    )
                )

    # Warn on CPIO without ordinance number
    for r in records:
        if r.doc_type == DOC_TYPE_OVERLAY_CPIO and not r.extracted_ordinance_number:
            issues.append(
                ZimasDocIssue(
                    step="doc_registry",
                    field=r.record_id,
                    severity="warning",
                    message=(
                        f"CPIO '{r.doc_label}' detected but ordinance number not yet confirmed. "
                        "Ordinance number is needed to fetch and verify subarea standards."
                    ),
                    action_required="Identify CPIO ordinance number from DCP or planning page.",
                    confidence_impact="degrades_to_unresolved",
                )
            )

    registry = LinkedDocRegistry(
        apn=apn,
        records=records,
        specific_plan_detected=specific_plan_detected,
        cpio_detected=cpio_detected,
        q_condition_detected=q_condition_detected,
        d_limitation_detected=d_limitation_detected,
        zi_document_detected=zi_document_detected,
        case_document_detected=case_document_detected,
        unresolved_count=unresolved_count,
        interrupt_doc_count=interrupt_doc_count,
        registry_confidence=registry_confidence,
        registry_input_coverage=input_coverage,
        issues=issues,
    )

    return registry, issues
