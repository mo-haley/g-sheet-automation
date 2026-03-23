"""Module gatekeeper — unresolved linked authority interrupt decisions.

Produces InterruptDecision objects for each calc topic based on the
LinkedDocRegistry state. One InterruptDecision per topic requested.

The gatekeeper does NOT modify any calc module output. It produces decisions
that a calc module (or its orchestrator) is responsible for acting on.

Interrupt level semantics:
    none           — no linked authority affects this topic
    provisional    — linked authority present but partial/minor; calc may proceed
                     with confidence capped at "provisional"
    unresolved     — linked authority present that likely governs this topic;
                     numeric result should not be surfaced with confidence >= medium
    refuse_to_decide — conflicting or irresolvable state; do not compute

blocking = True when interrupt_level is "unresolved" or "refuse_to_decide".

Interrupt rules by doc type and topic (conservative defaults):

    specific_plan:
        FAR, density, height, parking, setback → unresolved
        (specific plans can govern all dimensional and use standards)

    overlay_cpio:
        FAR, density, parking, setback → unresolved (if detected_not_interpreted)
        FAR, density, parking, setback → provisional (if surface_usable or better)
        height                         → provisional (CPIOs sometimes govern height)

    q_condition:
        all topics → provisional
        (Q conditions restrict uses and dimensions but scope is site-specific)

    d_limitation:
        density, FAR → provisional
        (D limitations reduce density/FAR; setback/parking rarely affected)

    case_document:
        all topics → provisional
        (case conditions may affect any topic; scope unknown until read)

    overlay_supplemental:
        parking, density → provisional
        (SUDs typically affect uses and parking; dimensional impact less common)

    zi_document:
        No interrupt generated. ZI documents provide informational context,
        not binding authority modifications. They may contain relevant info
        but do not interrupt calcs on their own.

    ordinance / pdf_artifact / map_figure_packet / planning_page:
        No interrupt generated unless doc_type_confidence is ambiguous
        and the doc could be a specific_plan or CPIO.

    unknown_artifact:
        all topics → provisional
        (Unknown artifact could be anything — treat conservatively)

    refuse_to_decide (any record):
        all topics → refuse_to_decide
        (Any irresolvable record poisons all topics)
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    LinkedDocRegistry,
    LinkedDocRecord,
    InterruptDecision,
    TriggerSummary,
    ZimasDocIssue,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_CASE_ZA,
    DOC_TYPE_CASE_CPC,
    DOC_TYPE_CASE_DIR,
    DOC_TYPE_CASE_ENV,
    DOC_TYPE_OVERLAY_SUPPLEMENTAL,
    DOC_TYPE_OVERLAY_CDO,
    DOC_TYPE_OVERLAY_HA,
    DOC_TYPE_OVERLAY_PO,
    DOC_TYPE_UNKNOWN_ARTIFACT,
    DOC_TYPE_ZI_DOCUMENT,
    CONF_SURFACE_USABLE,
    CONF_FETCHED_PARTIALLY_USABLE,
    CONF_REFUSE_TO_DECIDE,
    INTERRUPT_NONE,
    INTERRUPT_PROVISIONAL,
    INTERRUPT_UNRESOLVED,
    INTERRUPT_REFUSE,
    INPUT_COVERAGE_COMPLETE,
    INPUT_COVERAGE_THIN,
    INPUT_COVERAGE_UNCERTAIN,
    RIGOR_DETECTION_ONLY,
    RIGOR_IDENTITY_CONFIRMED,
    RIGOR_STRUCTURALLY_NARROWED,
    RIGOR_DOCUMENT_BACKED,
    RIGOR_AMBIGUOUS_IDENTITY,
    CONF_DETECTED_NOT_INTERPRETED,
    DQ_RETRIEVAL_CANDIDATE_ONLY,
    DQ_RETRIEVAL_NUMBER_KNOWN,
    DQ_RETRIEVAL_URL_KNOWN,
    DQ_RETRIEVAL_ZI_CORROBORATED,
    DQ_FEASIBILITY_NO_KNOWN_PATH,
    DQ_FEASIBILITY_BROWSER_ONLY,
    DQ_FEASIBILITY_ZI_MEDIATED,
    DQ_FEASIBILITY_URL_AVAILABLE,
)

_ALL_TOPICS = ("FAR", "density", "height", "parking", "setback")

_USABLE_STATES = {CONF_SURFACE_USABLE, CONF_FETCHED_PARTIALLY_USABLE}


def _rigor_level(record: LinkedDocRecord) -> str:
    """Return the RIGOR_* constant that best describes this record's identity
    and evidence quality.  This describes what is *known about* the authority —
    not what it says.  Logic is independent of interrupt level decisions."""
    if record.doc_type_confidence == "ambiguous":
        return RIGOR_AMBIGUOUS_IDENTITY
    cs = record.confidence_state
    if cs == CONF_REFUSE_TO_DECIDE:
        return RIGOR_AMBIGUOUS_IDENTITY
    if cs == CONF_FETCHED_PARTIALLY_USABLE:
        return RIGOR_DOCUMENT_BACKED
    if cs == CONF_SURFACE_USABLE:
        if record.extracted_chapter_list:
            return RIGOR_STRUCTURALLY_NARROWED
        return RIGOR_IDENTITY_CONFIRMED
    # detected_not_interpreted or detected_url_unverified
    return RIGOR_DETECTION_ONLY


_DQ_FEASIBILITY_HINTS: dict[str, str] = {
    # Human-readable next-step hints keyed by DQ_FEASIBILITY_* constant.
    # Accurate and conservative: never overstate what machine retrieval can do.
    # These appear at the end of _dq_identity_detail() strings.
    DQ_FEASIBILITY_ZI_MEDIATED: (
        "Next step: enable ZI fetch — the corroborating ZI document may contain "
        "the condition text inline."
    ),
    DQ_FEASIBILITY_URL_AVAILABLE: (
        "Next step: fetch the known URL to obtain the ordinance document."
    ),
    DQ_FEASIBILITY_BROWSER_ONLY: (
        "Next step: manual retrieval via LA City Clerk browser search "
        "(no machine-accessible URL pattern for LA ordinance PDFs)."
    ),
    DQ_FEASIBILITY_NO_KNOWN_PATH: (
        "Next step: confirm ordinance number from ZIMAS or LADBS "
        "before any retrieval is possible."
    ),
}


def _dq_identity_detail(record: LinkedDocRecord) -> str:
    """Return an ordinance_retrieval_status-aware rigor detail for Q/D records.

    Called only when rl == RIGOR_IDENTITY_CONFIRMED and the doc type is
    Q_CONDITION or D_LIMITATION.  Always explicit that ordinance content has
    not been read — stronger identity never implies interpreted restrictions.
    Appends a one-line feasibility hint so reviewers know the realistic next step.
    """
    status = record.ordinance_retrieval_status
    type_label = "Q condition" if record.doc_type == DOC_TYPE_Q_CONDITION else "D limitation"
    feasibility_hint = _DQ_FEASIBILITY_HINTS.get(record.dq_retrieval_feasibility, "")

    if status == DQ_RETRIEVAL_ZI_CORROBORATED:
        base = (
            f"Ordinance number confirmed from zone string parse "
            f"and independently corroborated by a fetched ZI document. "
            f"The {type_label} ordinance identity is strongly established. "
            "Restrictions have not been fetched or read — "
            "corroboration confirms identity only, not content."
        )
    elif status == DQ_RETRIEVAL_URL_KNOWN:
        base = (
            f"Ordinance number confirmed from zone string parse; "
            "a direct retrieval URL is known for this document. "
            f"The {type_label} ordinance can be fetched if needed. "
            "Restrictions have not been fetched or read."
        )
    else:
        # number_known (default for surface_usable without URL or corroboration)
        base = (
            f"Ordinance number confirmed from zone string parse. "
            f"The {type_label} ordinance is identified but not yet retrieved. "
            "Restrictions have not been fetched or read."
        )

    return f"{base} {feasibility_hint}".rstrip() if feasibility_hint else base


def _dq_retrieval_note(records: list[LinkedDocRecord]) -> str:
    """Return a one-line document-identity addendum for Q/D records in triggers.

    Appended to InterruptDecision.reason so reviewers can see at a glance
    what is known about each D/Q ordinance without having to inspect
    registry.records directly.

    Returns an empty string when no Q/D records are in the trigger list.
    Never overstates: always explicit that identity ≠ interpreted content.
    """
    dq = [
        r for r in records
        if r.doc_type in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION)
    ]
    if not dq:
        return ""

    parts: list[str] = []
    for r in dq:
        status = r.ordinance_retrieval_status
        label = r.doc_label
        if status == DQ_RETRIEVAL_ZI_CORROBORATED:
            parts.append(f"{label}: number corroborated by ZI (content unread)")
        elif status == DQ_RETRIEVAL_URL_KNOWN:
            parts.append(f"{label}: number confirmed, direct URL known (content unread)")
        elif status == DQ_RETRIEVAL_NUMBER_KNOWN:
            parts.append(f"{label}: number confirmed (content unread)")
        else:
            # candidate_only or empty (unfetched / no parse)
            parts.append(f"{label}: ordinance number not yet confirmed")

    return "D/Q identity: " + "; ".join(parts) + "."


def _rigor_detail(record: LinkedDocRecord) -> str:
    """Return a brief plain-English explanation of this record's rigor level.
    Content describes what is known *about* the authority, never its standards."""
    rl = _rigor_level(record)
    dt = record.doc_type

    if rl == RIGOR_AMBIGUOUS_IDENTITY:
        return "Document type or identity is uncertain. Conservative interrupt applied."

    if rl == RIGOR_DOCUMENT_BACKED:
        return (
            "Document surface fields extracted from fetch. "
            "Binding standards not interpreted."
        )

    if rl == RIGOR_STRUCTURALLY_NARROWED:
        return (
            "CPIO chapter structure confirmed; relevant subarea branch(es) identified. "
            "Standards not interpreted."
        )

    if rl == RIGOR_IDENTITY_CONFIRMED:
        if dt == DOC_TYPE_SPECIFIC_PLAN:
            return (
                "Plan name confirmed from ZIMAS structured field. "
                "Standards not fetched or interpreted."
            )
        if dt in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION):
            return _dq_identity_detail(record)
        if dt == DOC_TYPE_OVERLAY_CPIO:
            return (
                "CPIO identity confirmed. "
                "Chapter structure not yet extracted. "
                "Standards not interpreted."
            )
        return (
            "Document identity confirmed from verified source. "
            "Content not fetched or interpreted."
        )

    # RIGOR_DETECTION_ONLY — type-specific detail
    if dt in (DOC_TYPE_CASE_ZA, DOC_TYPE_CASE_CPC, DOC_TYPE_CASE_DIR,
              DOC_TYPE_CASE_ENV, DOC_TYPE_CASE_DOCUMENT):
        return "Case number detected; entitlement conditions not reviewed."
    if dt == DOC_TYPE_Q_CONDITION:
        return "Q condition detected; ordinance not confirmed. Restriction scope unknown."
    if dt == DOC_TYPE_D_LIMITATION:
        return "D limitation detected; ordinance not confirmed. Reduction amount unknown."
    if dt == DOC_TYPE_SPECIFIC_PLAN:
        return (
            "Plan presence inferred from overlay text. "
            "Plan name not confirmed from structured field."
        )
    if dt in (DOC_TYPE_OVERLAY_CDO, DOC_TYPE_OVERLAY_HA, DOC_TYPE_OVERLAY_PO,
              DOC_TYPE_OVERLAY_SUPPLEMENTAL):
        return "Overlay detected by name. Standards not interpreted."
    if dt == DOC_TYPE_OVERLAY_CPIO:
        return "CPIO detected by name. Subarea not confirmed. Standards not interpreted."
    return "Presence detected from field data. Identity and content not confirmed."


def _build_trigger_summary(record: LinkedDocRecord) -> TriggerSummary:
    """Build a TriggerSummary for one triggering LinkedDocRecord."""
    return TriggerSummary(
        record_id=record.record_id,
        doc_label=record.doc_label,
        doc_type=record.doc_type,
        rigor_level=_rigor_level(record),
        rigor_detail=_rigor_detail(record),
    )


def _zi_context_note(fetched_zi_records: list[LinkedDocRecord]) -> str:
    """Build a brief note about fetched ZI document evidence in the registry.

    Used to surface document-backed ZI context in interrupt rationale without
    promoting ZI records to interrupt triggers. ZI documents remain informational;
    this note only tells reviewers that ZI evidence is available and what it says.

    Returns an empty string when no fetched ZI records with titles exist.
    """
    titled = [r for r in fetched_zi_records if r.extracted_title]
    if not titled:
        return ""
    items = ", ".join(
        f"{r.doc_label} ({r.extracted_title!r})"
        for r in titled[:3]
    )
    suffix = f" (+{len(titled) - 3} more)" if len(titled) > 3 else ""
    return (
        f"Fetched ZI document evidence in registry: {items}{suffix}. "
        "ZI documents are informational — review for additional context."
    )


def evaluate_interrupts(
    registry: LinkedDocRegistry,
    topics: list[str] | None = None,
) -> tuple[list[InterruptDecision], list[ZimasDocIssue]]:
    """Produce one InterruptDecision per topic based on registry state.

    topics defaults to all standard calc topics.
    Returns (interrupt_decisions, issues).
    """
    if topics is None:
        topics = list(_ALL_TOPICS)

    issues: list[ZimasDocIssue] = []
    decisions: list[InterruptDecision] = []

    # Collect triggering records per topic (topic → list of (level, record))
    # We keep all triggers so the caller can see what drove the decision.
    triggers: dict[str, list[tuple[str, LinkedDocRecord]]] = {t: [] for t in topics}

    for record in registry.records:
        _apply_record_rules(record, topics, triggers)

    # ZI documents with fetched titles: informational context for rationale.
    # These do NOT interrupt — they inform the reviewer what ZI evidence is available.
    fetched_zi_records = [
        r for r in registry.records
        if r.doc_type == DOC_TYPE_ZI_DOCUMENT
        and r.confidence_state == CONF_FETCHED_PARTIALLY_USABLE
    ]
    zi_note = _zi_context_note(fetched_zi_records)

    for topic in topics:
        topic_triggers = triggers[topic]
        if not topic_triggers:
            # Qualify INTERRUPT_NONE with input coverage when the search was not complete.
            # The interrupt level stays INTERRUPT_NONE — we don't promote it here.
            # But an unqualified "nothing found" is false confidence when inputs were thin.
            coverage = registry.registry_input_coverage
            if coverage in (INPUT_COVERAGE_THIN, INPUT_COVERAGE_UNCERTAIN):
                reason = (
                    f"No linked authority items detected that govern {topic}. "
                    f"WARNING: registry input coverage is '{coverage}'. "
                    "This result should NOT be treated as evidence of no linked authority. "
                    "The search was incomplete — see registry input_coverage issues."
                )
                recommended_action = (
                    "Resolve input coverage gaps before relying on this interrupt level. "
                    "Review registry.issues for which detection sources were absent or failed."
                )
            elif coverage != INPUT_COVERAGE_COMPLETE:
                reason = (
                    f"No linked authority items detected that govern {topic}. "
                    f"Note: registry input coverage is '{coverage}' — "
                    "some detection sources were absent. Result may be incomplete."
                )
                recommended_action = (
                    "Review registry input coverage issues before treating this as confirmed."
                )
            else:
                reason = f"No linked authority items detected that govern {topic}."
                recommended_action = ""

            # Surface fetched ZI evidence even when no authority items interrupt.
            # A clean INTERRUPT_NONE is more trustworthy if ZI evidence corroborates
            # that we searched the parcel's ZI documents and found no blocking authority.
            if zi_note:
                reason += f" {zi_note}"

            decisions.append(
                InterruptDecision(
                    topic=topic,
                    interrupt_level=INTERRUPT_NONE,
                    reason=reason,
                    recommended_action=recommended_action,
                    blocking=False,
                )
            )
            continue

        # Aggregate: worst level wins
        level = _aggregate_level([t[0] for t in topic_triggers])
        triggering_records = [t[1] for t in topic_triggers]
        rigor_summaries = [_build_trigger_summary(r) for r in triggering_records]
        action = _build_action(level, topic, triggering_records)

        # Append fetched ZI context to the recommended action so reviewers
        # know what supporting document evidence is available.
        if zi_note:
            action += f" {zi_note}"

        decisions.append(
            InterruptDecision(
                topic=topic,
                interrupt_level=level,
                triggering_record_ids=[r.record_id for r in triggering_records],
                triggering_doc_labels=[r.doc_label for r in triggering_records],
                triggering_rigor=rigor_summaries,
                reason=_build_reason(level, topic, triggering_records, rigor_summaries),
                recommended_action=action,
                blocking=level in (INTERRUPT_UNRESOLVED, INTERRUPT_REFUSE),
            )
        )

    return decisions, issues


def _apply_record_rules(
    record: LinkedDocRecord,
    topics: list[str],
    triggers: dict[str, list[tuple[str, LinkedDocRecord]]],
) -> None:
    """Append (interrupt_level, record) to triggers for each affected topic."""
    dt = record.doc_type
    cs = record.confidence_state

    def _add(topic: str, level: str) -> None:
        if topic in triggers:
            triggers[topic].append((level, record))

    def _add_all(level: str) -> None:
        for t in topics:
            _add(t, level)

    # refuse_to_decide on any record → refuse on all topics
    if cs == CONF_REFUSE_TO_DECIDE:
        _add_all(INTERRUPT_REFUSE)
        return

    if dt == DOC_TYPE_SPECIFIC_PLAN:
        for t in topics:
            _add(t, INTERRUPT_UNRESOLVED)

    elif dt == DOC_TYPE_OVERLAY_CPIO:
        level = (
            INTERRUPT_PROVISIONAL
            if cs in _USABLE_STATES
            else INTERRUPT_UNRESOLVED
        )
        _add("FAR", level)
        _add("density", level)
        _add("parking", level)
        _add("setback", level)
        _add("height", INTERRUPT_PROVISIONAL)  # CPIOs less commonly govern height

    elif dt == DOC_TYPE_Q_CONDITION:
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_D_LIMITATION:
        _add("density", INTERRUPT_PROVISIONAL)
        _add("FAR", INTERRUPT_PROVISIONAL)

    # Case document subtypes — prefix-based scope routing.
    # Prefix is a scope hint only; case conditions have not been interpreted.
    # PROVISIONAL throughout: document not fetched; entitlement terms unknown.
    elif dt == DOC_TYPE_CASE_ZA:
        # ZA/AA decisions: variances, adjustments, CUPs.
        # Primarily govern setbacks, parking, height, and floor area.
        # Density excluded: ZA authority does not extend to density increases.
        _add("setback", INTERRUPT_PROVISIONAL)
        _add("parking", INTERRUPT_PROVISIONAL)
        _add("height", INTERRUPT_PROVISIONAL)
        _add("FAR", INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_CASE_CPC:
        # CPC/CF decisions: zone changes, GPAs, large CUPs, plan amendments.
        # CPC is the broadest case authority and may affect any dimensional standard.
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_CASE_DIR:
        # DIR decisions: interpretations and minor approvals.
        # Scope is variable and unpredictable; conservative all-topics fallback.
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_CASE_ENV:
        # ENV (CEQA Environmental Review): documents impacts but does not directly
        # impose dimensional standards. FAR and density flagged because ENV review
        # accompanies large entitlements that commonly modify these.
        # Parking, setback, height are not set by environmental review itself.
        _add("FAR", INTERRUPT_PROVISIONAL)
        _add("density", INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_CASE_DOCUMENT:
        # Unknown case prefix — conservative all-topics fallback.
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_OVERLAY_SUPPLEMENTAL:
        _add("parking", INTERRUPT_PROVISIONAL)
        _add("density", INTERRUPT_PROVISIONAL)

    # Recognized SUD subtypes — subtype detection improves interrupt routing.
    # PROVISIONAL throughout: documents not fetched; standards not interpreted.
    elif dt == DOC_TYPE_OVERLAY_CDO:
        # Coastal Development Overlay can govern FAR, density, height, setbacks,
        # and parking in Coastal Zone areas. All topics affected.
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_OVERLAY_HA:
        # Hillside Area standards govern FAR, height, and setbacks in hillside terrain.
        # All topics affected given the breadth of hillside dimensional controls.
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_OVERLAY_PO:
        # Pedestrian Oriented District primarily modifies parking minimums and front
        # setback requirements. FAR, density, and height are typically unaffected.
        _add("parking", INTERRUPT_PROVISIONAL)
        _add("setback", INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_UNKNOWN_ARTIFACT:
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    # zi_document, ordinance, pdf_artifact, map_figure_packet, planning_page:
    # no interrupt unless doc_type_confidence is ambiguous and could be CPIO/SP
    elif record.doc_type_confidence == "ambiguous":
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)


def _aggregate_level(levels: list[str]) -> str:
    """Return the worst (most restrictive) interrupt level from a list."""
    order = {
        INTERRUPT_REFUSE: 3,
        INTERRUPT_UNRESOLVED: 2,
        INTERRUPT_PROVISIONAL: 1,
        INTERRUPT_NONE: 0,
    }
    return max(levels, key=lambda lvl: order.get(lvl, 0))


def _build_reason(
    level: str,
    topic: str,
    records: list[LinkedDocRecord],
    rigor_summaries: list[TriggerSummary] | None = None,
) -> str:
    # Inline rigor tag per triggering record: "Venice SP [identity_confirmed]"
    if rigor_summaries:
        labeled = [
            f"{r.doc_label} [{rs.rigor_level}]"
            for r, rs in zip(records[:3], rigor_summaries[:3])
        ]
    else:
        labeled = [r.doc_label for r in records[:3]]
    labels = ", ".join(labeled)
    suffix = f" (and {len(records) - 3} more)" if len(records) > 3 else ""

    # D/Q identity addendum — appended to all non-refuse reasons when Q/D records
    # are present, so the reader sees document identity status inline with the
    # interrupt rationale rather than having to inspect registry.records separately.
    dq_note = _dq_retrieval_note(records)

    if level == INTERRUPT_REFUSE:
        return (
            f"One or more linked authority items for this parcel are in refuse_to_decide state. "
            f"Cannot compute {topic} with any confidence. Items: {labels}{suffix}"
        )
    if level == INTERRUPT_UNRESOLVED:
        base = (
            f"Linked authority item(s) that likely govern {topic} are detected but not interpreted: "
            f"{labels}{suffix}. "
            f"A numeric {topic} result cannot be confirmed until these are reviewed."
        )
        return f"{base} {dq_note}".rstrip() if dq_note else base
    base = (
        f"Linked authority item(s) that may affect {topic} are present but partially unresolved: "
        f"{labels}{suffix}. "
        f"{topic} calc may proceed with confidence capped at provisional."
    )
    return f"{base} {dq_note}".rstrip() if dq_note else base


def _build_action(
    level: str, topic: str, records: list[LinkedDocRecord]
) -> str:
    doc_types = {r.doc_type for r in records}
    if level == INTERRUPT_REFUSE:
        return (
            "Resolve the refuse_to_decide items in the linked-document registry "
            "before computing this topic."
        )
    if level == INTERRUPT_UNRESOLVED:
        actions = []
        if DOC_TYPE_SPECIFIC_PLAN in doc_types:
            actions.append("confirm specific plan subarea and applicable standards")
        if DOC_TYPE_OVERLAY_CPIO in doc_types:
            actions.append("confirm CPIO subarea and fetch/review CPIO ordinance")
        if not actions:
            actions.append("review linked authority documents listed above")
        return "Manual review required: " + "; ".join(actions) + "."
    return (
        f"Review linked authority items and confirm they do not govern {topic} "
        "before treating the calc result as confirmed."
    )
