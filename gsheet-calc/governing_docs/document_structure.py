"""Document structure parsing and branch selection.

Parses chapter/figure/appendix structure from governing documents,
then uses parcel context to select the relevant branches.

For CPIO-style documents: chapters map to subarea families.
The parcel's known subarea determines which chapter(s) to prioritize.

Does NOT interpret zoning rules. Only identifies document structure
and selects branches based on known parcel context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from governing_docs.document_context import (
    ContextField,
    ContextFieldConfidence,
    DocumentContext,
)


class BranchRelevance(str, Enum):
    """Why a branch is relevant or excluded."""
    PRIMARY = "primary"           # Direct subarea/chapter match
    GENERAL = "general"           # General provisions (e.g. Chapter I) that apply to all
    EXCLUDED = "excluded"         # Different subarea family
    UNCERTAIN = "uncertain"       # Cannot determine relevance


@dataclass
class DocumentChapter:
    """A chapter or major section in a governing document."""
    number: str             # "I", "II", "III", "1", "2", etc.
    title: str
    start_page: int | None = None
    end_page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    subarea_family: str | None = None  # e.g. "Central Commercial", "Regional Commercial"
    subareas: list[str] = field(default_factory=list)  # e.g. ["A", "B", "C", "D", "E"]


@dataclass
class DocumentFigure:
    """A figure in a governing document."""
    number: str             # "I", "II", "III-1", etc.
    title: str
    page: int | None = None
    subarea_family: str | None = None


@dataclass
class DocumentStructure:
    """Parsed outline of a governing document."""
    document_identifier: str
    chapters: list[DocumentChapter] = field(default_factory=list)
    figures: list[DocumentFigure] = field(default_factory=list)
    has_table_of_contents: bool = False
    structure_source: str = "parsed"  # "parsed", "known_reference", "manual"

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    def get_chapter_by_subarea(self, subarea_family: str) -> DocumentChapter | None:
        """Find a chapter that covers a given subarea family (case-insensitive)."""
        target = subarea_family.lower()
        for ch in self.chapters:
            if ch.subarea_family and target in ch.subarea_family.lower():
                return ch
        return None


@dataclass
class BranchMatch:
    """A single branch (chapter/figure) with its relevance determination."""
    branch_type: str        # "chapter", "figure", "appendix"
    number: str
    title: str
    relevance: BranchRelevance
    reason: str
    subarea_family: str | None = None
    page_range: str | None = None  # e.g. "pp. 15-30"


@dataclass
class BranchSelection:
    """Result of selecting branches from document structure using parcel context."""
    document_identifier: str

    # Context that drove the selection
    target_subarea: str | None = None
    target_subarea_source: str | None = None
    target_overlay: str | None = None

    # Selected branches
    primary_branches: list[BranchMatch] = field(default_factory=list)
    general_branches: list[BranchMatch] = field(default_factory=list)
    excluded_branches: list[BranchMatch] = field(default_factory=list)
    uncertain_branches: list[BranchMatch] = field(default_factory=list)

    # Audit
    warnings: list[str] = field(default_factory=list)
    conflict_weakened: bool = False

    @property
    def has_primary(self) -> bool:
        return len(self.primary_branches) > 0

    @property
    def is_ambiguous(self) -> bool:
        return not self.primary_branches and len(self.uncertain_branches) > 0


def select_branches(
    structure: DocumentStructure,
    context: DocumentContext,
) -> BranchSelection:
    """Select relevant branches from a document structure using parcel context.

    For each chapter/figure, determines whether it is:
    - PRIMARY: matches the parcel's known subarea family
    - GENERAL: applies to all parcels (e.g. general provisions)
    - EXCLUDED: covers a different subarea family
    - UNCERTAIN: cannot determine from available context
    """
    selection = BranchSelection(
        document_identifier=structure.document_identifier,
        target_subarea=context.subarea.value if context.has_subarea else None,
        target_subarea_source=context.subarea.source.value if context.has_subarea else None,
        target_overlay=context.overlay_name.value if context.has_overlay_name else None,
    )

    if not context.has_subarea:
        # Without a subarea, we can't select branches — everything is uncertain
        for ch in structure.chapters:
            selection.uncertain_branches.append(BranchMatch(
                branch_type="chapter",
                number=ch.number,
                title=ch.title,
                relevance=BranchRelevance.UNCERTAIN,
                reason="No subarea in parcel context — cannot determine chapter relevance.",
                subarea_family=ch.subarea_family,
            ))
        selection.warnings.append(
            "No subarea available in parcel context. All chapters are potentially relevant."
        )
        return selection

    # Check for conflict weakening
    if context.subarea.alternatives:
        selection.conflict_weakened = True
        alt_vals = [a.value for a in context.subarea.alternatives]
        selection.warnings.append(
            f"Subarea '{context.subarea.value}' has conflicting alternatives: "
            f"{', '.join(repr(v) for v in alt_vals)}. Branch selection may be unreliable."
        )

    target = context.subarea.value.lower()

    for ch in structure.chapters:
        page_range = None
        if ch.start_page and ch.end_page:
            page_range = f"pp. {ch.start_page}-{ch.end_page}"

        if not ch.subarea_family:
            # No subarea assigned — likely general provisions
            selection.general_branches.append(BranchMatch(
                branch_type="chapter",
                number=ch.number,
                title=ch.title,
                relevance=BranchRelevance.GENERAL,
                reason="Chapter has no subarea assignment — likely general provisions.",
                page_range=page_range,
            ))
            continue

        ch_family = ch.subarea_family.lower()

        if target in ch_family or ch_family in target:
            selection.primary_branches.append(BranchMatch(
                branch_type="chapter",
                number=ch.number,
                title=ch.title,
                relevance=BranchRelevance.PRIMARY,
                reason=(
                    f"Chapter covers subarea family '{ch.subarea_family}' which matches "
                    f"parcel subarea '{context.subarea.value}' "
                    f"(from {context.subarea.source.value})."
                ),
                subarea_family=ch.subarea_family,
                page_range=page_range,
            ))
        else:
            selection.excluded_branches.append(BranchMatch(
                branch_type="chapter",
                number=ch.number,
                title=ch.title,
                relevance=BranchRelevance.EXCLUDED,
                reason=(
                    f"Chapter covers subarea family '{ch.subarea_family}' which does not match "
                    f"parcel subarea '{context.subarea.value}'."
                ),
                subarea_family=ch.subarea_family,
                page_range=page_range,
            ))

    # Process figures
    for fig in structure.figures:
        if not fig.subarea_family:
            selection.general_branches.append(BranchMatch(
                branch_type="figure",
                number=fig.number,
                title=fig.title,
                relevance=BranchRelevance.GENERAL,
                reason="Figure has no subarea assignment.",
            ))
            continue

        fig_family = fig.subarea_family.lower()

        if target in fig_family or fig_family in target:
            selection.primary_branches.append(BranchMatch(
                branch_type="figure",
                number=fig.number,
                title=fig.title,
                relevance=BranchRelevance.PRIMARY,
                reason=f"Figure covers '{fig.subarea_family}' matching parcel subarea.",
                subarea_family=fig.subarea_family,
            ))
        else:
            selection.excluded_branches.append(BranchMatch(
                branch_type="figure",
                number=fig.number,
                title=fig.title,
                relevance=BranchRelevance.EXCLUDED,
                reason=f"Figure covers '{fig.subarea_family}', not parcel subarea.",
                subarea_family=fig.subarea_family,
            ))

    # Warn if no primary branches found
    if not selection.primary_branches:
        selection.warnings.append(
            f"No chapter or figure matches parcel subarea '{context.subarea.value}'. "
            f"The document may use different naming for this subarea."
        )

    return selection


# ── Known CPIO document structures ──────────────────────────────────

# These are manually confirmed from the real ordinance documents.
# They can be used without parsing the PDF when the CPIO is known.

def get_known_cpio_structure(cpio_name: str) -> DocumentStructure | None:
    """Return the known document structure for a CPIO district if available."""
    key = cpio_name.lower().replace(" cpio", "").strip()
    return _KNOWN_STRUCTURES.get(key)


_KNOWN_STRUCTURES: dict[str, DocumentStructure] = {
    "san pedro": DocumentStructure(
        document_identifier="San Pedro CPIO (Ord. 185539)",
        has_table_of_contents=True,
        structure_source="known_reference",
        chapters=[
            DocumentChapter(
                number="I",
                title="General Provisions",
                subarea_family=None,  # Applies to all subareas
            ),
            DocumentChapter(
                number="II",
                title="Regional Commercial Subarea",
                subarea_family="Regional Commercial",
                subareas=[],  # No lettered sub-subareas
            ),
            DocumentChapter(
                number="III",
                title="Central Commercial Subareas",
                subarea_family="Central Commercial",
                subareas=["A", "B", "C", "D", "E"],
            ),
            DocumentChapter(
                number="IV",
                title="Coastal Commercial Subareas",
                subarea_family="Coastal Commercial",
                subareas=["A", "B"],
            ),
            DocumentChapter(
                number="V",
                title="Multi-Family Residential Subarea",
                subarea_family="Multi-Family Residential",
                subareas=[],
            ),
            DocumentChapter(
                number="VI",
                title="Industrial Subareas",
                subarea_family="Industrial",
                subareas=["A", "B", "C", "D"],
            ),
        ],
        figures=[
            DocumentFigure(number="I", title="CPIO District Overview Map", subarea_family=None),
            DocumentFigure(number="II", title="Regional Commercial Subarea Map", subarea_family="Regional Commercial"),
            DocumentFigure(number="III", title="Central Commercial Subareas Map", subarea_family="Central Commercial"),
            DocumentFigure(number="IV", title="Coastal Commercial Subareas Map", subarea_family="Coastal Commercial"),
            DocumentFigure(number="V", title="Multi-Family Residential Subarea Map", subarea_family="Multi-Family Residential"),
            DocumentFigure(number="VI", title="Industrial Subareas Map", subarea_family="Industrial"),
        ],
    ),
}
