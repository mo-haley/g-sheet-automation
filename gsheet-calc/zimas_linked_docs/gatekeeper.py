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
    ZimasDocIssue,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_OVERLAY_SUPPLEMENTAL,
    DOC_TYPE_UNKNOWN_ARTIFACT,
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
)

_ALL_TOPICS = ("FAR", "density", "height", "parking", "setback")

_USABLE_STATES = {CONF_SURFACE_USABLE, CONF_FETCHED_PARTIALLY_USABLE}


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

        decisions.append(
            InterruptDecision(
                topic=topic,
                interrupt_level=level,
                triggering_record_ids=[r.record_id for r in triggering_records],
                triggering_doc_labels=[r.doc_label for r in triggering_records],
                reason=_build_reason(level, topic, triggering_records),
                recommended_action=_build_action(level, topic, triggering_records),
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

    elif dt == DOC_TYPE_CASE_DOCUMENT:
        for t in topics:
            _add(t, INTERRUPT_PROVISIONAL)

    elif dt == DOC_TYPE_OVERLAY_SUPPLEMENTAL:
        _add("parking", INTERRUPT_PROVISIONAL)
        _add("density", INTERRUPT_PROVISIONAL)

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
    level: str, topic: str, records: list[LinkedDocRecord]
) -> str:
    labels = ", ".join(r.doc_label for r in records[:3])
    suffix = f" (and {len(records) - 3} more)" if len(records) > 3 else ""
    if level == INTERRUPT_REFUSE:
        return (
            f"One or more linked authority items for this parcel are in refuse_to_decide state. "
            f"Cannot compute {topic} with any confidence. Items: {labels}{suffix}"
        )
    if level == INTERRUPT_UNRESOLVED:
        return (
            f"Linked authority item(s) that likely govern {topic} are detected but not interpreted: "
            f"{labels}{suffix}. "
            f"A numeric {topic} result cannot be confirmed until these are reviewed."
        )
    return (
        f"Linked authority item(s) that may affect {topic} are present but partially unresolved: "
        f"{labels}{suffix}. "
        f"{topic} calc may proceed with confidence capped at provisional."
    )


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
