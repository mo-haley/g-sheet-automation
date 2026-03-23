"""Document confidence state assignment.

Upgrades (or confirms) the confidence_state on each LinkedDocRecord based on
fetch status and extracted fields.

All records start at detected_not_interpreted (set by doc_classifier).
This module only upgrades confidence — it never downgrades a record that
already has refuse_to_decide.

Confidence state ladder (best to worst):
    fetched_partially_usable  — fetched + structural fields extracted
    surface_usable            — doc type confirmed + ordinance/ZI number confirmed
                                (no fetch required; number was in field data)
    detected_not_interpreted  — detected from field, no fetch, no extraction
    detected_url_unverified   — URL found but confidence is not direct_link
    refuse_to_decide          — conflicting signals or failed fetch with unexpected content

Upgrade rules:
    1. ZI document with confirmed ZI number in doc_label → surface_usable
       (ZI number from ZIMAS is reliable enough to mark as surface-usable
        without a fetch; the number is the durable identifier)
    2. Ordinance record with extracted_ordinance_number populated → surface_usable
    3. Any record with fetch_status=success + extracted_title → fetched_partially_usable
    4. Any record with fetch_status=failed → remains detected_not_interpreted,
       issue raised
    5. refuse_to_decide is sticky — never upgraded
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    LinkedDocRecord,
    ZimasDocIssue,
    DOC_TYPE_SPECIFIC_PLAN,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_ORDINANCE,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_Q_CONDITION,
    DOC_TYPE_D_LIMITATION,
    CONF_SURFACE_USABLE,
    CONF_FETCHED_PARTIALLY_USABLE,
    CONF_DETECTED_NOT_INTERPRETED,
    CONF_DETECTED_URL_UNVERIFIED,
    CONF_REFUSE_TO_DECIDE,
    URL_CONF_DIRECT_LINK,
    DQ_RETRIEVAL_CANDIDATE_ONLY,
    DQ_RETRIEVAL_NUMBER_KNOWN,
    DQ_RETRIEVAL_URL_KNOWN,
    DQ_FEASIBILITY_NO_KNOWN_PATH,
    DQ_FEASIBILITY_BROWSER_ONLY,
    DQ_FEASIBILITY_URL_AVAILABLE,
)

import re

_RE_ZI = re.compile(r"\bZI-\d{3,5}\b", re.IGNORECASE)
_RE_ORD = re.compile(r"\bO-\d{5,6}\b", re.IGNORECASE)


def assign_confidence_states(
    records: list[LinkedDocRecord],
) -> tuple[list[LinkedDocRecord], list[ZimasDocIssue]]:
    """Assign or upgrade confidence_state on each record.

    Returns the same records list (mutated in-place) + any issues.
    """
    issues: list[ZimasDocIssue] = []

    for record in records:
        # Never upgrade refuse_to_decide
        if record.confidence_state == CONF_REFUSE_TO_DECIDE:
            continue

        # Successful fetch with extracted title → fetched_partially_usable
        if record.fetch_status == "success" and record.extracted_title:
            record.confidence_state = CONF_FETCHED_PARTIALLY_USABLE
            continue

        # Failed fetch — keep at detected_not_interpreted, raise issue
        if record.fetch_status == "failed":
            record.confidence_state = CONF_DETECTED_NOT_INTERPRETED
            issues.append(
                ZimasDocIssue(
                    step="confidence",
                    field=record.record_id,
                    severity="error",
                    message=(
                        f"Fetch failed for {record.doc_label} ({record.doc_type}). "
                        f"Fetch notes: {record.fetch_notes or 'none'}. "
                        "Record remains at detected_not_interpreted."
                    ),
                    action_required="Retrieve document manually.",
                    confidence_impact="degrades_to_unresolved",
                )
            )
            continue

        # Specific plan: if the plan name was confirmed from the ZIMAS structured field
        # (doc_type_confidence == "confirmed"), the plan identity is known and stable.
        #
        # IMPORTANT: confirmed plan identity ≠ content interpretation.
        # The plan standards, subarea applicability, and any overrides have not been
        # read. Interrupt posture in gatekeeper.py remains INTERRUPT_UNRESOLVED on all
        # topics regardless of this upgrade — do not soften interrupt rules here.
        #
        # Records whose plan name was inferred from overlay_zones text carry
        # doc_type_confidence == "provisional" and stay at detected_not_interpreted.
        if record.doc_type == DOC_TYPE_SPECIFIC_PLAN:
            if record.doc_type_confidence == "confirmed":
                record.confidence_state = CONF_SURFACE_USABLE
                record.extraction_notes = (
                    f"Plan identity confirmed from ZIMAS structured field: "
                    f"{record.doc_label!r}. "
                    "Plan name is a stable ZIMAS-verified identifier. "
                    "Content not fetched or interpreted — "
                    "subarea applicability and plan standards are unknown."
                )
                continue

        # ZI document: if doc_label is a confirmed ZI number → surface_usable
        # The ZI number itself from ZIMAS is a reliable identifier.
        if record.doc_type == DOC_TYPE_ZI_DOCUMENT:
            if _RE_ZI.match(record.doc_label.strip()):
                record.confidence_state = CONF_SURFACE_USABLE
                continue

        # Q condition / D limitation: classify retrieval feasibility and, if a
        # stable ordinance number was confirmed by upstream parsing, upgrade to
        # surface_usable for identity confidence.
        #
        # IMPORTANT: stronger identity ≠ substantive interpretation.
        # The ordinance text has not been fetched or read. The restrictions it
        # contains are unknown. Do NOT weaken interrupt posture based on this
        # upgrade — gatekeeper.py Q/D rules produce INTERRUPT_PROVISIONAL on all
        # applicable topics regardless of confidence_state, intentionally.
        #
        # ordinance_retrieval_status ladder (best to worst):
        #   zi_corroborated   — set by orchestrator ZI cross-reference step (not here)
        #   url_known         — direct-link URL confirmed for this record
        #   number_known      — ordinance number confirmed; no direct URL yet
        #   candidate_only    — doc type detected; no number, no URL
        if record.doc_type in (DOC_TYPE_Q_CONDITION, DOC_TYPE_D_LIMITATION):
            if record.source_ordinance_number:
                record.confidence_state = CONF_SURFACE_USABLE
                record.extraction_notes = (
                    f"Ordinance identity confirmed: {record.source_ordinance_number}. "
                    "Document reference is stable. "
                    "Content not fetched or interpreted — "
                    "restrictions must be read from source ordinance."
                )
                # Retrieval URL available takes precedence over number-only.
                # A direct-link URL means machine fetch is possible (url_available).
                # Without a URL, only the City Clerk SPA browser path exists (browser_only).
                # See DQ_FEASIBILITY_* constants in models.py for ceiling rationale.
                if record.url and record.url_confidence == URL_CONF_DIRECT_LINK:
                    record.ordinance_retrieval_status = DQ_RETRIEVAL_URL_KNOWN
                    record.dq_retrieval_feasibility = DQ_FEASIBILITY_URL_AVAILABLE
                else:
                    record.ordinance_retrieval_status = DQ_RETRIEVAL_NUMBER_KNOWN
                    record.dq_retrieval_feasibility = DQ_FEASIBILITY_BROWSER_ONLY
            else:
                record.ordinance_retrieval_status = DQ_RETRIEVAL_CANDIDATE_ONLY
                record.dq_retrieval_feasibility = DQ_FEASIBILITY_NO_KNOWN_PATH
            continue  # always exit Q/D branch — no fallthrough to generic rules

        # CPIO: known-structure extraction populates extracted_chapter_list.
        # If chapters were extracted (from known-structure registry), the CPIO is
        # identified and its structure is confirmed — surface_usable is appropriate.
        # This fires before the ordinance-number check so chapter evidence takes
        # precedence; ordinance_number is also set by known-structure extraction.
        if record.doc_type == DOC_TYPE_OVERLAY_CPIO:
            if record.extracted_chapter_list:
                record.confidence_state = CONF_SURFACE_USABLE
                continue

        # Ordinance: if extracted_ordinance_number is set → surface_usable
        if record.doc_type in (DOC_TYPE_ORDINANCE, DOC_TYPE_OVERLAY_CPIO):
            if record.extracted_ordinance_number:
                record.confidence_state = CONF_SURFACE_USABLE
                continue
            # If doc_label looks like a confirmed ordinance number → surface_usable
            if _RE_ORD.match(record.doc_label.strip()):
                record.confidence_state = CONF_SURFACE_USABLE
                continue

        # URL present but not direct_link → detected_url_unverified
        # (This was set at classification time, but we confirm it here)
        if (
            record.url
            and record.url_confidence != "direct_link"
            and record.confidence_state == CONF_DETECTED_NOT_INTERPRETED
        ):
            record.confidence_state = CONF_DETECTED_URL_UNVERIFIED
            continue

        # Default: leave at whatever was set by classifier
        # (detected_not_interpreted for most records)

    return records, issues
