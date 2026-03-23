"""Surface-level structure extractor for fetched ZIMAS linked documents.

Extracts only reliable structural fields from raw document content:
    - title
    - ordinance number
    - chapter list / table of contents
    - figure labels
    - subarea names / families
    - plan or district name

Hard boundary: this module does NOT interpret rule content.
It does not read dimensional standards, use tables, or parking ratios.
Extraction stops at structural metadata.

For MVP, actual HTTP fetching is not implemented. This module defines:
    1. The extraction contract (what fields we try to extract and from what)
    2. A dry-run mode that marks records fetch_status = "skipped" with a note
    3. Stub extractors that will be replaced when fetching is added

When fetching is implemented, each extractor receives raw bytes (PDF or HTML)
and returns the surface fields only. No further parsing of extracted content
is performed — that is a separate human review step.

Supported doc types for extraction (MVP):
    - zi_document    → title, subject, ZI number confirmation, effective date
    - overlay_cpio   → ordinance number, subarea names, plan name
    - map_figure_packet → figure labels, district name
"""

from __future__ import annotations

from zimas_linked_docs.models import (
    LinkedDocRecord,
    ZimasDocIssue,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_MAP_FIGURE_PACKET,
    FETCH_NOW,
)


def extract_surface_fields(
    records: list[LinkedDocRecord],
    _fetch_enabled: bool = False,
) -> tuple[list[LinkedDocRecord], list[ZimasDocIssue]]:
    """Attempt surface-level extraction for fetch_now records.

    In MVP mode (_fetch_enabled=False), all records are marked skipped with
    an explanation. When fetching is added, _fetch_enabled=True activates
    doc-type-specific extractors.

    Returns the same records list (mutated in-place) + any issues.
    """
    issues: list[ZimasDocIssue] = []

    for record in records:
        if record.fetch_decision != FETCH_NOW:
            continue

        if not _fetch_enabled:
            record.fetch_status = "skipped"
            record.fetch_notes = (
                "HTTP fetching not yet implemented (MVP). "
                "Record marked fetch_now; extraction deferred until fetch layer is added."
            )
            issues.append(
                ZimasDocIssue(
                    step="structure_extractor",
                    field=record.record_id,
                    severity="info",
                    message=(
                        f"{record.doc_label} is a fetch_now candidate "
                        f"({record.doc_type}) but fetch layer is not active. "
                        "Extraction skipped."
                    ),
                    action_required="Enable fetch layer or retrieve document manually.",
                    confidence_impact="none",
                )
            )
            continue

        # When fetch layer is active, route to doc-type extractor.
        # Each extractor receives raw content bytes and returns nothing —
        # it mutates the record fields directly and sets fetch_status.
        if record.doc_type == DOC_TYPE_ZI_DOCUMENT:
            _extract_zi(record, issues)
        elif record.doc_type == DOC_TYPE_OVERLAY_CPIO:
            _extract_cpio(record, issues)
        elif record.doc_type == DOC_TYPE_MAP_FIGURE_PACKET:
            _extract_map_figure(record, issues)
        else:
            record.fetch_status = "skipped"
            record.fetch_notes = (
                f"No extractor implemented for doc_type={record.doc_type}. "
                "Record marked fetch_now by policy but extraction not supported."
            )

    return records, issues


# ── Stub extractors ───────────────────────────────────────────────────────────
# These define the extraction contract. Replace with real implementations
# when the fetch layer is added. Each extractor mutates record in-place.


def _extract_zi(record: LinkedDocRecord, issues: list[ZimasDocIssue]) -> None:
    """Extract surface fields from a LADBS ZI document.

    Target fields:
        extracted_title         — ZI document subject line
        extracted_ordinance_number — if ordinance reference is in header
        extraction_notes        — effective date, source note

    Source: LADBS ZI memo PDF or HTML at:
        https://ladbs.org/services/core-services/inspection-construction-services/zoning-information-files

    Stub: not yet implemented. Mark as skipped with contract note.
    """
    record.fetch_status = "skipped"
    record.fetch_notes = (
        "ZI extractor stub. "
        "When implemented: fetch from LADBS ZI lookup by ZI number in doc_label. "
        "Extract: subject/title, effective date, LAMC reference in header. "
        "Stop before rule content."
    )
    issues.append(
        ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="info",
            message=f"ZI extractor not yet implemented for {record.doc_label}.",
            action_required="Retrieve ZI document manually from LADBS and confirm subject line.",
            confidence_impact="none",
        )
    )


def _extract_cpio(record: LinkedDocRecord, issues: list[ZimasDocIssue]) -> None:
    """Extract surface fields from a CPIO ordinance PDF.

    Target fields:
        extracted_title         — ordinance title
        extracted_ordinance_number — confirm/extract ordinance number
        extracted_chapter_list  — table of contents chapter headings
        extracted_subarea_names — subarea names from section headings or table

    Stop before: subarea dimensional standards, use tables, parking ratios.

    Stub: not yet implemented.
    """
    record.fetch_status = "skipped"
    record.fetch_notes = (
        "CPIO extractor stub. "
        "When implemented: parse PDF TOC for chapter headings and subarea section names. "
        "Extract ordinance number from header. "
        "Stop before dimensional standards."
    )
    issues.append(
        ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="info",
            message=f"CPIO extractor not yet implemented for {record.doc_label}.",
            action_required=(
                "Retrieve CPIO ordinance manually. "
                "Confirm subarea placement before using any CPIO standards."
            ),
            confidence_impact="none",
        )
    )


def _extract_map_figure(record: LinkedDocRecord, issues: list[ZimasDocIssue]) -> None:
    """Extract surface fields from a map/figure packet PDF.

    Target fields:
        extracted_figure_labels — figure titles from PDF bookmarks or first-page text
        extracted_district_name — plan or district name from cover page

    Stop before: subarea boundary descriptions, dimensional controls on figures.

    Stub: not yet implemented.
    """
    record.fetch_status = "skipped"
    record.fetch_notes = (
        "Map/figure extractor stub. "
        "When implemented: extract PDF bookmark labels and cover page text. "
        "Stop before boundary coordinates or dimensional standards on figures."
    )
    issues.append(
        ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="info",
            message=f"Map/figure extractor not yet implemented for {record.doc_label}.",
            action_required="Retrieve map packet manually and confirm figure labels.",
            confidence_impact="none",
        )
    )
