"""CPIO document structure extraction for zimas_linked_docs.

Known-structure path only. Does NOT require HTTP fetch or file I/O.
Performs name normalisation, document structure lookup, and branch selection
based on parcel subarea context.

Isolation: no imports from governing_docs, calc/, rules/, setback/.
Logic is a self-contained reimplementation of the relevant portions of
governing_docs/document_structure.py (which cannot be imported).

Branch selection confidence:
    strong    — primary branches found (direct subarea family match)
    moderate  — only general branches apply (no subarea-specific chapters)
    weak      — subarea provided but no chapter match found; full doc surfaced
    uncertain — no subarea provided; cannot narrow
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Minimal structure types ───────────────────────────────────────────────────
# Replicated from governing_docs/document_structure.py without import.

@dataclass
class _CPIOChapter:
    number: str
    title: str
    subarea_family: str | None = None
    subareas: list[str] = field(default_factory=list)
    # Page span within the ordinance document (1-indexed, inclusive).
    # None means the span has not been recorded yet — not that the chapter
    # does not exist. Fill in from the verified ordinance PDF when available.
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class _CPIOFigure:
    number: str
    title: str
    subarea_family: str | None = None
    # Page span within the ordinance document (1-indexed, inclusive).
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class _CPIOStructure:
    document_identifier: str
    ordinance_number: str | None = None
    chapters: list[_CPIOChapter] = field(default_factory=list)
    figures: list[_CPIOFigure] = field(default_factory=list)


# ── Branch-aware entry types ──────────────────────────────────────────────────

@dataclass
class BranchEntryData:
    """One chapter or figure in a branch selection result, with page-span data.

    Internal to cpio_fetch. Converted to models.BranchEntry by structure_extractor.
    label matches the format used in branch_*_labels:
        "Chapter I: General Provisions", "Figure II: Regional Commercial Subarea Map"

    page_start and page_end are 1-indexed document page numbers.
    span_known=False means page data was not recorded — not that the item is
    on page 0. When span data is added to _CPIOChapter/_CPIOFigure, span_known
    is set to True automatically (when page_start is not None).
    """
    label: str
    page_start: int | None = None
    page_end: int | None = None
    span_known: bool = False


@dataclass
class BranchSelectionResult:
    """Structured output of _select_branches(), replacing the 5-tuple return.

    Contains both string labels (backward-compatible with existing record fields)
    and BranchEntryData objects (page-aware, consumed by structure_extractor to
    build models.BranchWorkingSet).
    """
    primary_labels: list[str] = field(default_factory=list)
    general_labels: list[str] = field(default_factory=list)
    excluded_labels: list[str] = field(default_factory=list)
    primary_entries: list[BranchEntryData] = field(default_factory=list)
    general_entries: list[BranchEntryData] = field(default_factory=list)
    excluded_entries: list[BranchEntryData] = field(default_factory=list)
    confidence: str = "uncertain"
    notes: str = ""


# ── Extraction result ─────────────────────────────────────────────────────────

@dataclass
class CPIOExtractionResult:
    """Result of a CPIO known-structure extraction attempt."""
    doc_label: str

    # Resolved structure metadata
    resolved_name: str | None = None        # canonical key matched in registry
    ordinance_number: str | None = None
    document_identifier: str | None = None

    # Document structure (human-readable labels)
    chapter_labels: list[str] = field(default_factory=list)
    figure_labels: list[str] = field(default_factory=list)

    # Branch selection — string labels (backward-compatible)
    branch_primary_labels: list[str] = field(default_factory=list)
    branch_general_labels: list[str] = field(default_factory=list)
    branch_excluded_labels: list[str] = field(default_factory=list)
    branch_selection_confidence: str = "uncertain"  # strong/moderate/weak/uncertain
    branch_selection_notes: str = ""

    # Branch selection — page-aware entries (consumed by structure_extractor)
    branch_primary_entries: list[BranchEntryData] = field(default_factory=list)
    branch_general_entries: list[BranchEntryData] = field(default_factory=list)
    branch_excluded_entries: list[BranchEntryData] = field(default_factory=list)

    # Working-set summary derived from branch entries
    span_coverage: str = "none"      # "full" / "partial" / "none"
    working_set_summary: str = ""    # plain-English; empty when extraction not attempted

    # conflict_weakened: True when subarea context OR a same-structure overlay-name
    # conflict weakened branch selection (confidence capped at "moderate").
    conflict_weakened: bool = False

    # identity_contested: True when the context overlay_name resolves to a *different*
    # canonical structure than doc_label. Document identity cannot be confirmed;
    # subarea matching was skipped and confidence is forced to "weak".
    identity_contested: bool = False

    # Extraction status
    extraction_status: str = "not_attempted"  # success / not_found
    extraction_notes: str = ""

    # Non-fatal issues
    issues: list[str] = field(default_factory=list)


# ── Known CPIO structures ─────────────────────────────────────────────────────
# Manually confirmed from real ordinance documents.
# Keys are normalised names (lowercase, CPIO suffix stripped, stripped).

_KNOWN_STRUCTURES: dict[str, _CPIOStructure] = {
    "san pedro": _CPIOStructure(
        document_identifier="San Pedro CPIO (Ord. 185539)",
        ordinance_number="185539",
        chapters=[
            _CPIOChapter("I", "General Provisions", subarea_family=None),
            _CPIOChapter("II", "Regional Commercial Subarea", "Regional Commercial"),
            _CPIOChapter("III", "Central Commercial Subareas", "Central Commercial", ["A", "B", "C", "D", "E"]),
            _CPIOChapter("IV", "Coastal Commercial Subareas", "Coastal Commercial", ["A", "B"]),
            _CPIOChapter("V", "Multi-Family Residential Subarea", "Multi-Family Residential"),
            _CPIOChapter("VI", "Industrial Subareas", "Industrial", ["A", "B", "C", "D"]),
        ],
        figures=[
            _CPIOFigure("I", "CPIO District Overview Map", None),
            _CPIOFigure("II", "Regional Commercial Subarea Map", "Regional Commercial"),
            _CPIOFigure("III", "Central Commercial Subareas Map", "Central Commercial"),
            _CPIOFigure("IV", "Coastal Commercial Subareas Map", "Coastal Commercial"),
            _CPIOFigure("V", "Multi-Family Residential Subarea Map", "Multi-Family Residential"),
            _CPIOFigure("VI", "Industrial Subareas Map", "Industrial"),
        ],
    ),
}

# Alias normalisation — alternative names that should resolve to a canonical key.
_ALIASES: dict[str, str] = {
    "coastal san pedro": "san pedro",
}


# ── Name normalisation ────────────────────────────────────────────────────────

def _normalize_cpio_name(name: str) -> str:
    """Normalise a CPIO name for known-structure lookup.

    Strips trailing CPIO-related suffixes, lowercases, strips whitespace.
    """
    s = name.lower().strip()
    for suffix in (" cpio", " community plan implementation overlay", " overlay"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _resolve_cpio_name(doc_label: str) -> tuple[str | None, _CPIOStructure | None]:
    """Return (canonical_key, structure) or (None, None) if unknown."""
    key = _normalize_cpio_name(doc_label)
    structure = _KNOWN_STRUCTURES.get(key)
    if structure is not None:
        return key, structure
    canonical = _ALIASES.get(key)
    if canonical:
        structure = _KNOWN_STRUCTURES.get(canonical)
        if structure is not None:
            return canonical, structure
    return None, None


# ── Branch selection ──────────────────────────────────────────────────────────

def _ch_entry(ch: _CPIOChapter) -> BranchEntryData:
    """Build a BranchEntryData from a chapter definition."""
    return BranchEntryData(
        label=f"Chapter {ch.number}: {ch.title}",
        page_start=ch.page_start,
        page_end=ch.page_end,
        span_known=ch.page_start is not None,
    )


def _fig_entry(fig: _CPIOFigure) -> BranchEntryData:
    """Build a BranchEntryData from a figure definition."""
    return BranchEntryData(
        label=f"Figure {fig.number}: {fig.title}",
        page_start=fig.page_start,
        page_end=fig.page_end,
        span_known=fig.page_start is not None,
    )


def _compute_span_coverage(entries: list[BranchEntryData]) -> str:
    """Return 'full', 'partial', or 'none' based on how many entries have page spans."""
    if not entries:
        return "none"
    known = sum(1 for e in entries if e.span_known)
    if known == len(entries):
        return "full"
    if known > 0:
        return "partial"
    return "none"


def _working_set_summary(sel: "BranchSelectionResult") -> str:
    """Build a plain-English working-set summary from a BranchSelectionResult.

    Summarises the effective working set (primary + general), the excluded count,
    and whether page spans are available. The conflict-weakened flag is surfaced
    explicitly so reviewers cannot miss it.
    """
    n_primary = len(sel.primary_labels)
    n_general = len(sel.general_labels)
    n_excluded = len(sel.excluded_labels)
    effective = sel.primary_entries + sel.general_entries
    span_cov = _compute_span_coverage(effective)
    span_note = (
        "page spans available" if span_cov == "full" else
        "partial page spans" if span_cov == "partial" else
        "page spans not available"
    )

    if sel.confidence == "uncertain":
        total = n_general
        return (
            f"Working set undetermined — no subarea provided. "
            f"Full document ({total} items) in scope. "
            "Provide cpio_subarea for narrowed working set."
        )

    if sel.confidence == "weak":
        return (
            f"Subarea not matched — full document ({n_general} items) "
            f"in effective scope. 0 branches excluded. {span_note}."
        )

    # strong or moderate
    conflict_note = " [CONFLICT-WEAKENED: confidence capped at moderate]" if sel.confidence == "moderate" else ""

    def _brief(labels: list[str]) -> str:
        parts = [lbl.split(": ", 1)[1] if ": " in lbl else lbl for lbl in labels[:2]]
        suffix = f" +{len(labels) - 2} more" if len(labels) > 2 else ""
        return ", ".join(parts) + suffix

    parts = []
    if n_primary:
        s = "s" if n_primary != 1 else ""
        parts.append(f"{n_primary} primary branch{s} ({_brief(sel.primary_labels)})")
    if n_general:
        s = "s" if n_general != 1 else ""
        parts.append(f"{n_general} general branch{s} ({_brief(sel.general_labels)})")

    ws_desc = " + ".join(parts) if parts else "no branches"
    excl_s = "es" if n_excluded != 1 else ""
    excl_note = f"{n_excluded} branch{excl_s} excluded"

    return (
        f"Effective working set: {ws_desc}{conflict_note}; "
        f"{excl_note}. {span_note}."
    )


def _select_branches(
    structure: _CPIOStructure,
    cpio_subarea: str | None,
    conflict_weakened: bool = False,
    identity_contested: bool = False,
) -> BranchSelectionResult:
    """Select branches from known structure using parcel subarea context.

    Returns a BranchSelectionResult containing both string labels (for backward
    compatibility with existing record fields) and BranchEntryData objects
    (page-aware, for building BranchWorkingSet in structure_extractor).

    Matching logic: substring match between normalised subarea and chapter
    subarea_family. "Central Commercial-C" matches "Central Commercial" because
    "central commercial" is a substring of "central commercial-c".

    conflict_weakened: when True, a primary match is capped at "moderate"
    rather than "strong". The match is still performed using the primary
    subarea value, but the caller is signalling that the value is contested.

    identity_contested: when True, document identity cannot be confirmed —
    the context overlay_name resolves to a different structure than doc_label.
    Subarea matching is skipped entirely; all chapters surface as general with
    confidence forced to "weak". This is a stronger signal than conflict_weakened.
    """
    sel = BranchSelectionResult()

    # Identity contested: skip subarea matching, surface full structure as general.
    if identity_contested:
        all_entries = (
            [_ch_entry(ch) for ch in structure.chapters] +
            [_fig_entry(fig) for fig in structure.figures]
        )
        sel.general_labels = [e.label for e in all_entries]
        sel.general_entries = all_entries
        sel.confidence = "weak"
        sel.notes = (
            "Document identity contested: overlay name in narrowing context resolves to "
            "a different CPIO than the detected document. Subarea matching skipped. "
            "Full structure surfaced for manual review."
        )
        return sel

    if not cpio_subarea:
        # No subarea context — all chapters potentially relevant
        for ch in structure.chapters:
            e = _ch_entry(ch)
            sel.general_labels.append(e.label)
            sel.general_entries.append(e)
        for fig in structure.figures:
            e = _fig_entry(fig)
            sel.general_labels.append(e.label)
            sel.general_entries.append(e)
        sel.confidence = "uncertain"
        sel.notes = (
            "No subarea provided. All chapters treated as potentially relevant. "
            "Provide cpio_subarea for narrowed branch selection."
        )
        return sel

    target = cpio_subarea.lower()

    for ch in structure.chapters:
        e = _ch_entry(ch)
        if ch.subarea_family is None:
            sel.general_labels.append(e.label)
            sel.general_entries.append(e)
        elif target in ch.subarea_family.lower() or ch.subarea_family.lower() in target:
            sel.primary_labels.append(e.label)
            sel.primary_entries.append(e)
        else:
            sel.excluded_labels.append(e.label)
            sel.excluded_entries.append(e)

    for fig in structure.figures:
        e = _fig_entry(fig)
        if fig.subarea_family is None:
            sel.general_labels.append(e.label)
            sel.general_entries.append(e)
        elif target in fig.subarea_family.lower() or fig.subarea_family.lower() in target:
            sel.primary_labels.append(e.label)
            sel.primary_entries.append(e)
        else:
            sel.excluded_labels.append(e.label)
            sel.excluded_entries.append(e)

    if sel.primary_labels:
        if conflict_weakened:
            sel.confidence = "moderate"
            sel.notes = (
                f"Subarea '{cpio_subarea}' matched {len(sel.primary_labels)} primary branch(es). "
                "General branches also apply. "
                "WARNING: subarea context has conflicting alternatives — "
                "confidence capped at 'moderate'. Manual review recommended."
            )
        else:
            sel.confidence = "strong"
            sel.notes = (
                f"Subarea '{cpio_subarea}' matched {len(sel.primary_labels)} primary branch(es). "
                "General branches also apply."
            )
    else:
        # Subarea was provided but nothing matched — surface full doc, warn
        sel.confidence = "weak"
        sel.notes = (
            f"Subarea '{cpio_subarea}' did not match any chapter subarea family. "
            "All chapters are potentially relevant. "
            "Verify subarea name against CPIO document."
        )
        # Surface all as general so caller sees the full document
        sel.general_labels = sel.excluded_labels + sel.general_labels
        sel.general_entries = sel.excluded_entries + sel.general_entries
        sel.excluded_labels = []
        sel.excluded_entries = []

    return sel


# ── Main entry point ──────────────────────────────────────────────────────────

def run_cpio_extraction(
    doc_label: str,
    cpio_subarea: str | None = None,
    conflict_weakened: bool = False,
    identity_contested: bool = False,
) -> CPIOExtractionResult:
    """Extract CPIO document structure using the known-structure registry.

    Pure dict lookup — no HTTP, no file I/O. Safe to call unconditionally.

    Args:
        doc_label:          CPIO name as detected (e.g. "San Pedro CPIO", "Venice CPIO")
        cpio_subarea:       parcel's subarea string (e.g. "Central Commercial-C"), optional
        conflict_weakened:  when True, branch confidence capped at "moderate".
                            Pass True when subarea or same-structure overlay-name
                            conflict is present in the NarrowingContext.
        identity_contested: when True, document identity cannot be confirmed.
                            Subarea matching is skipped; all chapters surface as
                            general with confidence forced to "weak". Pass True when
                            the context overlay_name resolves to a different CPIO
                            than doc_label.

    Returns CPIOExtractionResult with structure metadata and branch selection.
    extraction_status values: "success" / "not_found"
    """
    result = CPIOExtractionResult(doc_label=doc_label)

    resolved_key, structure = _resolve_cpio_name(doc_label)

    if structure is None:
        result.extraction_status = "not_found"
        result.extraction_notes = (
            f"'{doc_label}' not found in known CPIO structure registry. "
            "Structure extraction not available. Manual review required."
        )
        result.issues.append(
            f"Unknown CPIO structure for '{doc_label}'. "
            "Add to known-structure registry when ordinance is confirmed."
        )
        return result

    result.resolved_name = resolved_key
    result.document_identifier = structure.document_identifier
    result.ordinance_number = structure.ordinance_number

    result.chapter_labels = [
        f"Chapter {ch.number}: {ch.title}" for ch in structure.chapters
    ]
    result.figure_labels = [
        f"Figure {fig.number}: {fig.title}" for fig in structure.figures
    ]

    sel = _select_branches(
        structure, cpio_subarea,
        conflict_weakened=conflict_weakened,
        identity_contested=identity_contested,
    )
    result.branch_primary_labels = sel.primary_labels
    result.branch_general_labels = sel.general_labels
    result.branch_excluded_labels = sel.excluded_labels
    result.branch_selection_confidence = sel.confidence
    result.branch_selection_notes = sel.notes
    result.branch_primary_entries = sel.primary_entries
    result.branch_general_entries = sel.general_entries
    result.branch_excluded_entries = sel.excluded_entries
    result.conflict_weakened = conflict_weakened
    result.identity_contested = identity_contested

    effective_entries = sel.primary_entries + sel.general_entries
    result.span_coverage = _compute_span_coverage(effective_entries)
    result.working_set_summary = _working_set_summary(sel)

    result.extraction_status = "success"
    parts = [
        f"Known structure: {structure.document_identifier}",
        f"Chapters: {len(structure.chapters)}, Figures: {len(structure.figures)}",
        f"Branch selection: {sel.confidence}",
    ]
    if cpio_subarea and not identity_contested:
        parts.append(f"Subarea context: '{cpio_subarea}'")
    if conflict_weakened:
        parts.append("conflict_weakened=True")
    if identity_contested:
        parts.append("identity_contested=True")
    result.extraction_notes = "; ".join(parts)

    return result
