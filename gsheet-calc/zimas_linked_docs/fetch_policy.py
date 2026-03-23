"""Fetch policy for ZIMAS linked documents.

Assigns FetchDecision (fetch_now / defer / never) to each LinkedDocRecord.

Rules are conservative:
- Fetch only when the document type is structured enough to yield usable
  surface fields (title, ordinance number, chapter list, subarea names).
- Never fetch documents whose contents cannot be acted on without human review.
- Never fetch documents whose URL confidence is not direct_link.
- Confidence_interrupter_only items are never fetched — their presence is
  the signal, not their content.

Fetch-now priority scale (lower = higher priority):
    1  ZI document with confirmed ZI number
    2  CPIO overlay with direct PDF URL
    3  Map/figure packet with direct PDF URL

All other cases: defer or never.

Important: this module assigns decisions. It does not perform any HTTP
requests. Actual fetching is handled by a separate fetch layer (not yet
implemented for MVP).
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    LinkedDocRecord,
    FetchDecision,
    ZimasDocIssue,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_MAP_FIGURE_PACKET,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_PLANNING_PAGE,
    DOC_TYPE_UNKNOWN_ARTIFACT,
    POSTURE_CONFIDENCE_INTERRUPTER_ONLY,
    URL_CONF_DIRECT_LINK,
    FETCH_NOW,
    FETCH_DEFER,
    FETCH_NEVER,
)

# Doc types that should never be fetched for MVP interpretation
_NEVER_FETCH_TYPES = {
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    DOC_TYPE_CASE_DOCUMENT,
    DOC_TYPE_PLANNING_PAGE,
    DOC_TYPE_UNKNOWN_ARTIFACT,
}


def assign_fetch_decisions(
    records: list[LinkedDocRecord],
) -> tuple[list[FetchDecision], list[ZimasDocIssue]]:
    """Assign fetch decisions to all records.

    Returns (fetch_decisions, issues).
    Mutates record.fetch_decision in-place so the registry stays consistent.
    """
    decisions: list[FetchDecision] = []
    issues: list[ZimasDocIssue] = []

    for record in records:
        decision, reason, priority = _evaluate(record)

        record.fetch_decision = decision
        decisions.append(
            FetchDecision(
                record_id=record.record_id,
                decision=decision,
                reason=reason,
                priority=priority,
            )
        )

    return decisions, issues


def _evaluate(record: LinkedDocRecord) -> tuple[str, str, int]:
    """Return (decision, reason, priority) for one record."""

    # Confidence_interrupter_only: never fetch — presence is the signal
    if record.usability_posture == POSTURE_CONFIDENCE_INTERRUPTER_ONLY:
        return (
            FETCH_NEVER,
            (
                f"{record.doc_type} items are confidence interrupters only. "
                "Fetching would imply content interpretation. Presence alone is the signal."
            ),
            0,
        )

    # Explicit never-fetch doc types
    if record.doc_type in _NEVER_FETCH_TYPES:
        return (
            FETCH_NEVER,
            f"{record.doc_type} documents are deferred to manual review for MVP.",
            0,
        )

    # ZI document: fetch now if we have a ZI number (URL is inferred from pattern)
    if record.doc_type == DOC_TYPE_ZI_DOCUMENT:
        # ZI number is in doc_label (e.g., "ZI-2374")
        if record.doc_label.upper().startswith("ZI-"):
            return (
                FETCH_NOW,
                (
                    "ZI document with confirmed number. LADBS ZI lookup provides "
                    "structured title and subject. Highest-value fetch target."
                ),
                1,
            )
        return (
            FETCH_DEFER,
            "ZI document detected but number not confirmed. Defer until number is verified.",
            0,
        )

    # CPIO: fetch now only if we have a direct PDF URL to the ordinance
    if record.doc_type == DOC_TYPE_OVERLAY_CPIO:
        if record.url and record.url_confidence == URL_CONF_DIRECT_LINK:
            return (
                FETCH_NOW,
                (
                    "CPIO ordinance with direct PDF URL. "
                    "Fetch to extract subarea table and ordinance number."
                ),
                2,
            )
        return (
            FETCH_DEFER,
            (
                "CPIO detected but no direct PDF URL available. "
                "Locate ordinance PDF from DCP before fetching."
            ),
            0,
        )

    # Map/figure packet: fetch now only with direct PDF URL
    if record.doc_type == DOC_TYPE_MAP_FIGURE_PACKET:
        if record.url and record.url_confidence == URL_CONF_DIRECT_LINK:
            return (
                FETCH_NOW,
                "Map/figure packet with direct PDF URL. Fetch to extract figure labels.",
                3,
            )
        return (
            FETCH_DEFER,
            "Map/figure packet detected but no direct PDF URL. Defer.",
            0,
        )

    # PDF artifact: defer unless URL is direct link
    if record.url and record.url_confidence == URL_CONF_DIRECT_LINK:
        return (
            FETCH_DEFER,
            (
                "Direct PDF link available but document type not confirmed. "
                "Classify document type before deciding whether to fetch."
            ),
            0,
        )

    # Default: defer
    return (
        FETCH_DEFER,
        f"No fetch criteria met for doc_type={record.doc_type}. Defer to manual review.",
        0,
    )
