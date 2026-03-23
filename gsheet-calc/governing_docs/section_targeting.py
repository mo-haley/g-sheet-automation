"""Section targeting — use parcel context to find relevant document sections.

Given extracted document text and a DocumentContext, identifies which
sections/pages/paragraphs are most relevant to the specific parcel.

Does NOT interpret zoning rules. Only identifies where to look.

Auditability: every TargetingResult carries the context values used,
their provenance, any conflicting alternatives, page numbers, and
explicit reasons for inclusion or exclusion of each section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from governing_docs.document_context import (
    ContextField,
    ContextFieldConfidence,
    ContextFieldSource,
    DocumentContext,
)


class MatchStrength(str):
    """How strong a section match is."""
    EXACT = "exact"        # Section heading names the subarea/district exactly
    PARTIAL = "partial"    # Section text contains boost terms
    NEGATIVE = "negative"  # Section appears to be for a different subarea/district


@dataclass
class ContextValueUsed:
    """Records a context value that was used for targeting, with provenance."""
    field_name: str         # e.g. "subarea", "overlay_name"
    value: str
    source: str             # ContextFieldSource.value
    confidence: str         # ContextFieldConfidence.value
    source_detail: str | None = None
    has_conflict: bool = False
    conflicting_values: list[str] = field(default_factory=list)


@dataclass
class SectionMatch:
    """A section of document text that matches parcel context."""
    section_heading: str | None
    page_number: int | None
    start_offset: int
    end_offset: int
    match_strength: str  # MatchStrength value
    matched_terms: list[str] = field(default_factory=list)
    text_preview: str = ""
    inclusion_reason: str | None = None   # Why this section was included
    exclusion_reason: str | None = None   # Why this section was excluded
    char_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class TargetingResult:
    """Result of targeting relevant sections within a document."""
    document_identifier: str
    context_narrowing_strength: str
    total_pages: int = 0
    total_chars: int = 0

    # Context values that drove the targeting (for audit)
    context_values_used: list[ContextValueUsed] = field(default_factory=list)

    # Targeted sections
    relevant_sections: list[SectionMatch] = field(default_factory=list)
    excluded_sections: list[SectionMatch] = field(default_factory=list)

    # Warnings
    warnings: list[str] = field(default_factory=list)

    # Flags
    narrowing_weakened_by_conflict: bool = False

    @property
    def has_exact_match(self) -> bool:
        return any(s.match_strength == MatchStrength.EXACT for s in self.relevant_sections)

    @property
    def is_ambiguous(self) -> bool:
        return not self.relevant_sections and self.context_narrowing_strength != "none"

    @property
    def relevant_char_count(self) -> int:
        return sum(s.char_count for s in self.relevant_sections)


def find_relevant_sections(
    full_text: str,
    context: DocumentContext,
    document_identifier: str = "",
    page_texts: list[str] | None = None,
) -> TargetingResult:
    """Find sections of a document that are relevant to the parcel context.

    Strategy:
    1. Record which context values are being used for targeting
    2. Split text into sections by headings (page-aware if page_texts provided)
    3. Score each section against boost terms from context
    4. Flag sections for a different subarea/district as excluded, with reasons
    5. Warn if known subarea/overlay cannot be found
    6. Warn if narrowing depends on a conflicted context value
    """
    result = TargetingResult(
        document_identifier=document_identifier,
        context_narrowing_strength=context.narrowing_strength,
        total_chars=len(full_text),
        total_pages=len(page_texts) if page_texts else 0,
    )

    # ── Record context values used for audit ─────────────────────────
    _record_context_values_used(context, result)

    if not full_text.strip():
        result.warnings.append("Document text is empty.")
        return result

    if context.narrowing_strength == "none":
        result.warnings.append(
            "No narrowing context available — entire document is potentially relevant."
        )
        return result

    # ── Check if narrowing depends on conflicted values ──────────────
    _check_conflict_weakening(context, result)

    # ── Build page offset map for page-number assignment ─────────────
    page_offset_map = _build_page_offset_map(page_texts) if page_texts else None

    # ── Split into sections ──────────────────────────────────────────
    sections = _split_into_sections(full_text)

    # ── Build term sets ──────────────────────────────────────────────
    boost_terms = [t.lower() for t in context.search_boost_terms if t]
    subarea_term = context.subarea.value.lower() if context.has_subarea else None
    subarea_display = context.subarea.value if context.has_subarea else None

    doc_subareas = _find_all_subareas_in_text(full_text) if subarea_term else set()
    subarea_found_in_doc = False

    for heading, start, end in sections:
        section_text = full_text[start:end]
        section_lower = section_text.lower()
        heading_lower = (heading or "").lower()

        matched: list[str] = []
        strength = MatchStrength.PARTIAL
        inclusion_reason = None
        exclusion_reason = None

        # Check for exact subarea match
        if subarea_term and subarea_term in heading_lower:
            strength = MatchStrength.EXACT
            matched.append(f"subarea '{subarea_display}' in heading")
            inclusion_reason = f"Heading contains target subarea '{subarea_display}'"
            subarea_found_in_doc = True
        elif subarea_term and subarea_term in section_lower:
            matched.append(f"subarea '{subarea_display}' in body")
            inclusion_reason = f"Body text contains target subarea '{subarea_display}'"
            subarea_found_in_doc = True

        # Check for other boost terms
        for term in boost_terms:
            if term != subarea_term and term in section_lower:
                matched.append(term)

        if matched and not inclusion_reason:
            inclusion_reason = f"Contains boost terms: {', '.join(matched)}"

        # Check for different subarea (exclusion)
        if subarea_term and doc_subareas:
            other_subareas = doc_subareas - {subarea_term}
            for other in other_subareas:
                if other in heading_lower and subarea_term not in heading_lower:
                    strength = MatchStrength.NEGATIVE
                    exclusion_reason = (
                        f"Heading contains different subarea '{other}' "
                        f"while target subarea is '{subarea_display}'"
                    )
                    break

        # Assign page number
        page_num = _offset_to_page(start, page_offset_map) if page_offset_map else None

        preview = section_text[:200].strip()
        section_chars = end - start

        match = SectionMatch(
            section_heading=heading,
            page_number=page_num,
            start_offset=start,
            end_offset=end,
            match_strength=strength,
            matched_terms=matched,
            text_preview=preview,
            inclusion_reason=inclusion_reason,
            exclusion_reason=exclusion_reason,
            char_count=section_chars,
        )

        if strength == MatchStrength.NEGATIVE:
            result.excluded_sections.append(match)
        elif matched:
            result.relevant_sections.append(match)

    # ── Missing subarea/overlay warnings ─────────────────────────────
    if subarea_term and not subarea_found_in_doc:
        result.warnings.append(
            f"Subarea '{subarea_display}' (from {context.subarea.source.value}) "
            f"was not found in the document text. The document may use different "
            f"naming or the subarea may not be applicable."
        )

    if context.has_overlay_name:
        overlay_lower = context.overlay_name.value.lower()
        if overlay_lower not in full_text.lower():
            result.warnings.append(
                f"Overlay name '{context.overlay_name.value}' was not found in "
                f"the document text. This document may not be for this overlay."
            )

    return result


def _record_context_values_used(
    context: DocumentContext,
    result: TargetingResult,
) -> None:
    """Record which context values are being used, with provenance."""
    for field_name, ctx_field in [
        ("subarea", context.subarea),
        ("overlay_name", context.overlay_name),
        ("ordinance_number", context.ordinance_number),
        ("specific_plan_name", context.specific_plan_name),
        ("community_plan_area", context.community_plan_area),
        ("base_zone", context.base_zone),
    ]:
        if ctx_field and ctx_field.value:
            conflicting = [alt.value for alt in ctx_field.alternatives]
            result.context_values_used.append(ContextValueUsed(
                field_name=field_name,
                value=ctx_field.value,
                source=ctx_field.source.value,
                confidence=ctx_field.confidence.value,
                source_detail=ctx_field.source_detail,
                has_conflict=len(conflicting) > 0,
                conflicting_values=conflicting,
            ))


def _check_conflict_weakening(
    context: DocumentContext,
    result: TargetingResult,
) -> None:
    """Warn if the primary narrowing field has conflicting alternatives."""
    # The subarea is the strongest narrower — if it's conflicted, targeting is weakened
    if context.has_subarea and context.subarea.alternatives:
        alt_values = [a.value for a in context.subarea.alternatives]
        result.narrowing_weakened_by_conflict = True
        result.warnings.append(
            f"Targeting uses subarea '{context.subarea.value}' "
            f"(from {context.subarea.source.value}), but conflicting value(s) exist: "
            f"{', '.join(repr(v) for v in alt_values)}. "
            f"Narrowed result may not be fully trustworthy."
        )

    if context.has_overlay_name and context.overlay_name.alternatives:
        alt_values = [a.value for a in context.overlay_name.alternatives]
        result.narrowing_weakened_by_conflict = True
        result.warnings.append(
            f"Targeting uses overlay name '{context.overlay_name.value}' "
            f"(from {context.overlay_name.source.value}), but conflicting value(s) exist: "
            f"{', '.join(repr(v) for v in alt_values)}."
        )


def _build_page_offset_map(page_texts: list[str]) -> list[tuple[int, int, int]]:
    """Build a map of (page_number, start_offset, end_offset) from page texts.

    Reconstructs offsets assuming pages are joined with double-newline.
    """
    page_map: list[tuple[int, int, int]] = []
    offset = 0
    for i, text in enumerate(page_texts):
        page_start = offset
        page_end = offset + len(text)
        page_map.append((i + 1, page_start, page_end))
        offset = page_end + 2  # Account for "\n\n" between pages
    return page_map


def _offset_to_page(
    char_offset: int,
    page_map: list[tuple[int, int, int]],
) -> int | None:
    """Find which page a character offset falls in."""
    for page_num, start, end in page_map:
        if start <= char_offset < end:
            return page_num
    # Past the last page boundary — assign to last page
    if page_map:
        return page_map[-1][0]
    return None


def _split_into_sections(text: str) -> list[tuple[str | None, int, int]]:
    """Split text into sections based on heading-like patterns."""
    heading_pattern = re.compile(
        r"^("
        r"[A-Z][A-Z ,.\-/()]{10,}"
        r"|(?:Section|Article|Chapter|Part|Appendix)\s+[\dA-Z][^\n]*"
        r"|[A-Z][^\n]*(?:Subareas?|Sub-?areas?|Districts?|Overlay)[^\n]*"
        r")$",
        re.MULTILINE,
    )

    headings = list(heading_pattern.finditer(text))

    if not headings:
        return [(None, 0, len(text))]

    sections = []
    for i, m in enumerate(headings):
        heading_text = m.group(1).strip()
        start = m.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections.append((heading_text, start, end))

    return sections


def _find_all_subareas_in_text(text: str) -> set[str]:
    """Find all subarea-like names mentioned in the text."""
    matches = re.findall(
        r"(?:subarea|sub-?area)\s+[\"']?([A-Za-z][A-Za-z\s]+?)(?:[\"',.\s]|$)"
        r"|([A-Za-z][A-Za-z\s]+?)\s+(?:subarea|sub-?area)",
        text,
        re.IGNORECASE,
    )
    result = set()
    for groups in matches:
        for g in groups:
            cleaned = g.strip().lower()
            if cleaned and len(cleaned) > 1:
                result.add(cleaned)
    return result
