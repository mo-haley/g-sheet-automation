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
    BranchEntry,
    BranchWorkingSet,
    DOC_TYPE_ZI_DOCUMENT,
    DOC_TYPE_OVERLAY_CPIO,
    DOC_TYPE_MAP_FIGURE_PACKET,
    FETCH_NOW,
    POSTURE_MANUAL_REVIEW_FIRST,
)
from zimas_linked_docs.zi_fetch import run_zi_fetch, extract_zi_number
from zimas_linked_docs.cpio_fetch import CPIOExtractionResult, run_cpio_extraction, _resolve_cpio_name
from zimas_linked_docs.narrowing_context import NarrowingContext


def extract_surface_fields(
    records: list[LinkedDocRecord],
    _fetch_enabled: bool = False,
    _cache_dir: "Path | None" = None,
    _narrowing_context: "NarrowingContext | None" = None,
) -> tuple[list[LinkedDocRecord], list[ZimasDocIssue]]:
    """Attempt surface-level extraction for fetch_now records.

    In MVP mode (_fetch_enabled=False), all records are marked skipped with
    an explanation. When fetching is added, _fetch_enabled=True activates
    doc-type-specific extractors.

    _cache_dir: optional override for the ZI document cache directory.
    Primarily for testing; production callers omit this (default cache used).

    _narrowing_context: assembled parcel narrowing context for CPIO branch
    selection. Carries subarea, overlay_name, and conflict information with
    source provenance. Pass None to skip context-aware narrowing (all CPIO
    chapters surface as general). Built by the orchestrator from ZimasLinkedDocInput.

    Returns the same records list (mutated in-place) + any issues.
    """
    issues: list[ZimasDocIssue] = []

    for record in records:
        # CPIO known-structure extraction runs unconditionally — pure dict lookup,
        # no HTTP required. fetch_decision is irrelevant here: CPIO records typically
        # receive FETCH_DEFER from fetch_policy (no direct URL available), but the
        # known-structure path does not need a URL. Gating it behind fetch_decision
        # would silently skip extraction for every CPIO without a URL — including
        # San Pedro, the only currently-registered known structure.
        if record.doc_type == DOC_TYPE_OVERLAY_CPIO:
            _extract_cpio(record, issues, narrowing_context=_narrowing_context)
            continue

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
            _extract_zi(record, issues, cache_dir=_cache_dir)
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


def _extract_zi(
    record: LinkedDocRecord,
    issues: list[ZimasDocIssue],
    cache_dir: "Path | None" = None,
) -> None:
    """Fetch and extract surface fields from a LADBS ZI document.

    Calls run_zi_fetch() which handles URL construction, HEAD verification,
    GET fetch/cache, and minimal pdfplumber header extraction.

    Populates on the record:
        fetch_attempted, fetch_status, fetch_notes
        extracted_title, extracted_ordinance_number, extraction_notes

    cache_dir: optional override; if None, run_zi_fetch uses the default cache.
    """
    result = run_zi_fetch(doc_label=record.doc_label, cache_dir=cache_dir)
    record.fetch_attempted = True

    if result.fetch_status == "failed":
        record.fetch_status = "failed"
        record.fetch_notes = result.fetch_notes
        # Note in doc_type_notes that title was not confirmed.
        record.doc_type_notes = (
            record.doc_type_notes.rstrip(".") +
            ". Fetch failed — title not confirmed by document retrieval."
        )
        issues.append(ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="error",
            message=f"ZI fetch failed for {record.doc_label}: {result.fetch_notes}",
            action_required="Retrieve ZI document manually from LADBS.",
            confidence_impact="none",  # confidence.py responds to fetch_status=failed
        ))
        return

    record.fetch_status = "success"
    record.fetch_notes = result.fetch_notes
    record.extracted_title = result.extracted_title
    record.extracted_ordinance_number = result.extracted_ordinance_number

    # ── doc_type_notes enrichment ────────────────────────────────────────────
    # Update doc_type_notes with fetched evidence so the record communicates
    # what the ZI is specifically about, not just that it was detected.
    # The field is provenance-only — downstream modules do not read it.
    # Three sub-cases: good title, no title, extraction failed.
    if result.extracted_title and result.extraction_quality == "good":
        subject_hint = _zi_subject_hint(result.extracted_title)
        record.doc_type_notes = (
            record.doc_type_notes.rstrip(".") +
            f". Fetched title: {result.extracted_title!r}." +
            (f" ZI subject: {subject_hint}." if subject_hint else "")
        )
    elif result.fetch_status == "success" and not result.extracted_title:
        record.doc_type_notes = (
            record.doc_type_notes.rstrip(".") +
            f". Fetch succeeded but title was not extracted"
            f" (quality: {result.extraction_quality}). Title unconfirmed by fetch."
        )

    # Build structured provenance in extraction_notes.
    # All sourced from ZI PDF header (first page only, first 500 chars).
    # Absence of ordinance or title is noted explicitly — callers should not
    # infer absence from a missing note.
    provenance_parts: list[str] = [
        f"Source: ZI PDF header (quality: {result.extraction_quality})"
    ]
    if result.extracted_title:
        provenance_parts.append(f"Title: {result.extracted_title!r}")
    else:
        provenance_parts.append("Title: not in header")
    if result.extracted_ordinance_number:
        provenance_parts.append(f"Ordinance: {result.extracted_ordinance_number}")
    else:
        provenance_parts.append("Ordinance: not in header")
    if result.extracted_effective_date:
        provenance_parts.append(f"Effective date: {result.extracted_effective_date}")
    if result.url_verify_notes:
        provenance_parts.append(f"URL verify: {result.url_verify_notes}")

    record.extraction_notes = "; ".join(provenance_parts)

    # ── ZI number cross-check ────────────────────────────────────────────────
    # If the PDF header contains a ZI number that differs from the ZI number
    # we were expecting, we fetched the wrong document (or ZIMAS data is wrong).
    # Consequence: title and ordinance from this fetch are unreliable.
    # Clear extracted_title so confidence.py does not promote to
    # fetched_partially_usable on bad data.
    if result.header_zi_number:
        expected_digits = extract_zi_number(record.doc_label)
        if expected_digits and result.header_zi_number != expected_digits:
            record.extracted_title = None
            record.extraction_notes += (
                f"; CONFLICT: expected ZI-{expected_digits},"
                f" PDF header says ZI {result.header_zi_number}"
            )
            # Downgrade classification: document identity cannot be confirmed.
            # MACHINE_USABLE is no longer appropriate when we can't verify which
            # ZI we have. Downgrade to MANUAL_REVIEW_FIRST and mark ambiguous.
            record.usability_posture = POSTURE_MANUAL_REVIEW_FIRST
            record.doc_type_confidence = "ambiguous"
            record.doc_type_notes = (
                record.doc_type_notes.rstrip(".") +
                f". CLASSIFICATION DOWNGRADED: ZI number mismatch —"
                f" expected ZI-{expected_digits},"
                f" PDF header says ZI {result.header_zi_number}."
                " Document identity unconfirmed. Manual review required."
            )
            issues.append(ZimasDocIssue(
                step="structure_extractor",
                field=record.record_id,
                severity="error",
                message=(
                    f"ZI number mismatch for {record.doc_label}: "
                    f"expected ZI-{expected_digits} but PDF header says "
                    f"ZI {result.header_zi_number}. "
                    "Title and ordinance from this fetch are unreliable. "
                    "Record downgraded to manual_review_first / ambiguous."
                ),
                action_required=(
                    "Verify ZI document identity. "
                    "Fetched PDF may not correspond to the expected ZI record."
                ),
                confidence_impact="degrades_to_unresolved",
            ))

    # ── Ordinance conflict check ─────────────────────────────────────────────
    # If an ordinance number was detected from ZIMAS field data (source_ordinance_number)
    # AND a different ordinance appears in the PDF header, flag the discrepancy.
    # Both values are preserved; the caller must reconcile manually.
    if (
        record.source_ordinance_number
        and result.extracted_ordinance_number
        and record.source_ordinance_number != result.extracted_ordinance_number
    ):
        record.extraction_notes += (
            f"; CONFLICT: detected ordinance {record.source_ordinance_number}"
            f" vs PDF header ordinance {result.extracted_ordinance_number}"
        )
        issues.append(ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="warning",
            message=(
                f"Ordinance mismatch for {record.doc_label}: "
                f"detected source ordinance {record.source_ordinance_number!r} "
                f"differs from PDF header ordinance "
                f"{result.extracted_ordinance_number!r}. "
                "Manual verification required."
            ),
            action_required=(
                "Confirm which ordinance number governs this ZI document. "
                "Both values preserved in extraction_notes."
            ),
            confidence_impact="degrades_to_provisional",
        ))

    # ── Extraction quality warning ───────────────────────────────────────────
    if result.extraction_quality in ("weak", "failed"):
        issues.append(ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="warning",
            message=(
                f"{record.doc_label} fetched but PDF extraction quality was "
                f"'{result.extraction_quality}'. "
                f"Extracted title: {result.extracted_title or 'none'}."
            ),
            action_required="Manually verify ZI document contents.",
            confidence_impact="none",
        ))


def _zi_subject_hint(title: str) -> str:
    """Return a brief subject classification hint from a ZI title string.

    Purely informational — identifies broad subject category so the record
    communicates what the ZI is about without interpreting its contents.
    Returns an empty string when no recognizable subject can be inferred.
    """
    t = title.upper()
    if "COMMUNITY PLAN IMPLEMENTATION OVERLAY" in t or "CPIO" in t:
        return "CPIO-related"
    if "SPECIFIC PLAN" in t:
        return "specific plan area"
    if "COASTAL" in t:
        return "coastal zone"
    if "HISTORIC" in t or "PRESERVATION" in t:
        return "historic preservation"
    if "ENTERPRISE ZONE" in t:
        return "enterprise zone"
    if "PARKING" in t:
        return "parking"
    if "HEIGHT" in t or "HEIGHTS" in t:
        return "height regulation"
    return ""


def _build_branch_working_set(result: "CPIOExtractionResult") -> BranchWorkingSet:
    """Convert a CPIOExtractionResult into a BranchWorkingSet model.

    Called after a successful known-structure extraction to produce the
    consolidated, page-aware working-set representation on the record.
    Translates BranchEntryData objects (cpio_fetch internals) into
    BranchEntry Pydantic models (models.py externals).
    """
    def _to_entries(data_list: list) -> list[BranchEntry]:
        return [
            BranchEntry(
                label=e.label,
                page_start=e.page_start,
                page_end=e.page_end,
                span_known=e.span_known,
            )
            for e in data_list
        ]

    return BranchWorkingSet(
        primary=_to_entries(result.branch_primary_entries),
        general=_to_entries(result.branch_general_entries),
        excluded=_to_entries(result.branch_excluded_entries),
        selection_confidence=result.branch_selection_confidence,
        conflict_weakened=result.conflict_weakened or result.identity_contested,
        span_coverage=result.span_coverage,
        working_set_summary=result.working_set_summary,
    )


def _extract_cpio(
    record: LinkedDocRecord,
    issues: list[ZimasDocIssue],
    narrowing_context: "NarrowingContext | None" = None,
) -> None:
    """Extract surface fields from a CPIO using the known-structure registry.

    Known-structure path: pure dict lookup, no HTTP required.
    Populates:
        extracted_chapter_list       — chapter labels from known structure
        extracted_figure_labels      — figure labels from known structure
        extracted_ordinance_number   — from known structure if available
        branch_primary_labels        — chapters/figures matching parcel subarea
        branch_general_labels        — general-provisions chapters (apply to all)
        branch_excluded_labels       — chapters for other subareas
        branch_selection_confidence  — "strong"/"moderate"/"weak"/"uncertain"
        branch_selection_notes       — plain-language branch selection rationale
        branch_conflict_weakened     — True when narrowing context had conflicts
        extraction_notes             — structured provenance

    When the CPIO is not in the known-structure registry, records are left at
    their prior state and an info issue is raised for manual review.

    narrowing_context: assembled parcel context from build_narrowing_context().
    Carries subarea value and conflict flags. Pass None to skip narrowing.
    """
    # Resolve subarea and conflict flags from NarrowingContext.
    # If context is absent, behave as before (uncertain branch selection).
    if narrowing_context and narrowing_context.has_subarea:
        subarea_value = narrowing_context.subarea.value
        conflict_weakened = narrowing_context.subarea_has_conflict
    else:
        subarea_value = None
        conflict_weakened = False

    # Check overlay identity against context.
    # Two cases:
    #   1. Primary overlay_name resolves to a DIFFERENT canonical structure than
    #      doc_label → identity_contested: skip subarea matching, force "weak".
    #   2. Primary overlay_name matches doc_label but alternatives exist →
    #      conflict_weakened: cap at "moderate" (same as subarea conflict).
    identity_contested = False
    if narrowing_context and narrowing_context.has_overlay_name:
        doc_key, _ = _resolve_cpio_name(record.doc_label)
        ctx_key, _ = _resolve_cpio_name(narrowing_context.overlay_name.value)
        if doc_key is not None and ctx_key is not None and doc_key != ctx_key:
            # Context's best-evidence overlay name resolves to a different structure.
            identity_contested = True
        elif narrowing_context.overlay_name_has_conflict:
            # Primary overlay name matches doc_label but alternatives exist.
            conflict_weakened = True

    result = run_cpio_extraction(
        doc_label=record.doc_label,
        cpio_subarea=subarea_value,
        conflict_weakened=conflict_weakened,
        identity_contested=identity_contested,
    )

    if result.extraction_status == "not_found":
        record.fetch_status = "skipped"
        record.fetch_notes = result.extraction_notes
        issues.append(
            ZimasDocIssue(
                step="structure_extractor",
                field=record.record_id,
                severity="info",
                message=(
                    f"CPIO '{record.doc_label}' not in known-structure registry. "
                    "Structure extraction not available."
                ),
                action_required=(
                    "Retrieve CPIO ordinance manually. "
                    "Confirm subarea placement before using any CPIO standards."
                ),
                confidence_impact="none",
            )
        )
        return

    # Known-structure extraction succeeded.
    # Treat as fetch_status="success" since structure was obtained.
    record.fetch_status = "success"
    record.fetch_notes = result.extraction_notes
    record.extraction_notes = result.extraction_notes

    record.extracted_chapter_list = result.chapter_labels
    record.extracted_figure_labels = result.figure_labels

    # Ordinance number from known structure (more reliable than detection-time value)
    if result.ordinance_number:
        record.extracted_ordinance_number = result.ordinance_number

    # Raise a warning issue when overlay identity is contested.
    # The issue surfaces the contradiction explicitly — the record alone does not
    # contain enough context to reconstruct why identity was contested.
    if result.identity_contested and narrowing_context and narrowing_context.has_overlay_name:
        issues.append(ZimasDocIssue(
            step="structure_extractor",
            field=record.record_id,
            severity="warning",
            message=(
                f"Overlay identity mismatch for {record.doc_label!r}: "
                f"context overlay_name is '{narrowing_context.overlay_name.value}' "
                f"(source: {narrowing_context.overlay_name.source}), "
                "which resolves to a different CPIO structure than the detected document. "
                "Subarea matching skipped; branch confidence forced to 'weak'."
            ),
            action_required=(
                "Verify which CPIO governs this parcel. "
                "The detected document label and the overlay_zones field disagree."
            ),
            confidence_impact="degrades_to_unresolved",
        ))

    # Branch selection
    record.branch_primary_labels = result.branch_primary_labels
    record.branch_general_labels = result.branch_general_labels
    record.branch_excluded_labels = result.branch_excluded_labels
    record.branch_selection_confidence = result.branch_selection_confidence
    record.branch_selection_notes = result.branch_selection_notes
    # branch_conflict_weakened reflects both subarea conflict and identity contest.
    record.branch_conflict_weakened = result.conflict_weakened or result.identity_contested

    # Append narrowing-context conflict detail to branch_selection_notes.
    # This makes the conflict visible on the record without inspecting the
    # NarrowingContext object, which is not persisted.
    if narrowing_context and narrowing_context.conflicts:
        conflict_summary = "; ".join(narrowing_context.conflicts)
        record.branch_selection_notes += f" Narrowing conflicts detected: {conflict_summary}"

    # Consolidated branch working-set (page-aware, for downstream consumers)
    record.branch_working_set = _build_branch_working_set(result)

    # Non-fatal registry issues (e.g. alias resolution notes)
    for msg in result.issues:
        issues.append(
            ZimasDocIssue(
                step="structure_extractor",
                field=record.record_id,
                severity="info",
                message=msg,
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
