"""Typed narrowing context for ZIMAS linked-document extraction.

Assembles available parcel/ZIMAS context into a source-ranked, conflict-aware
object that drives document narrowing (branch selection, subarea targeting).

Design:
  - Each context field carries a value, its source, confidence, and any
    alternative values from lower-priority or conflicting sources.
  - When two sources disagree, both values are preserved. The higher-priority
    source is primary. Conflicts are recorded explicitly.
  - Conflict-bearing contexts weaken downstream branch selection confidence
    rather than silently picking one value and discarding the other.

Source priority (lower number = higher priority):
  1. caller_explicit      — cpio_subarea explicitly set by the calling pipeline
  2. zimas_profile_field  — structured ZIMAS field data (overlay_zones, etc.)
  3. zone_string_parse    — inferred from zone string parse output
  4. zi_document_header   — extracted from a fetched ZI PDF header

Isolation: no imports from governing_docs, calc/, rules/, setback/.
Parallel to (but independent of) governing_docs/document_context.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zimas_linked_docs.models import ZimasLinkedDocInput


# ── Source priority ───────────────────────────────────────────────────────────

# Source constants exposed for callers that build NarrowingContext manually.
SOURCE_CALLER_EXPLICIT = "caller_explicit"
SOURCE_ZIMAS_PROFILE_FIELD = "zimas_profile_field"
SOURCE_ZONE_STRING_PARSE = "zone_string_parse"
SOURCE_ZI_DOCUMENT_HEADER = "zi_document_header"

_SOURCE_PRIORITY: dict[str, int] = {
    SOURCE_CALLER_EXPLICIT:     1,
    SOURCE_ZIMAS_PROFILE_FIELD: 2,
    SOURCE_ZONE_STRING_PARSE:   3,
    SOURCE_ZI_DOCUMENT_HEADER:  4,
}


# ── Context field types ───────────────────────────────────────────────────────

@dataclass
class NarrowingAlternative:
    """A non-primary value for a context field, preserved for audit."""
    value: str
    source: str                 # one of SOURCE_* constants
    confidence: str             # "high" / "medium" / "low"
    source_detail: str = ""
    reason_not_primary: str = ""


@dataclass
class NarrowingContextField:
    """A single narrowing-context field with provenance and alternatives."""
    value: str
    source: str                 # one of SOURCE_* constants
    confidence: str             # "high" / "medium" / "low"
    source_detail: str = ""
    alternatives: list[NarrowingAlternative] = field(default_factory=list)


@dataclass
class NarrowingContext:
    """Assembled parcel narrowing context for document extraction.

    Carries known facts relevant to targeting the correct branch within a
    multi-part document (e.g. CPIO subarea, overlay district name).

    Each field has explicit source provenance. Conflicts between sources
    are recorded and exposed as a flag, so downstream steps can weaken
    their confidence rather than silently use possibly-wrong data.

    Fields:
        subarea                 — CPIO subarea for branch selection
        overlay_name            — CPIO/overlay district name from ZIMAS fields
        ordinance_number        — reserved; not yet populated
        specific_plan_subarea   — specific plan subdistrict/subarea from ZIMAS fields
                                  CONTEXT PRESERVATION ONLY: no specific-plan
                                  structure extraction exists yet. Carrying this
                                  field does not enable plan interpretation and
                                  does not affect interrupt posture or confidence.
                                  When structure extraction for specific plans is
                                  implemented, it should read this field.
    """
    subarea: NarrowingContextField | None = None
    overlay_name: NarrowingContextField | None = None
    ordinance_number: NarrowingContextField | None = None

    # Context preservation for future specific-plan structure extraction.
    # Not consumed by any current extractor. See class docstring.
    specific_plan_subarea: NarrowingContextField | None = None

    # Conflict records — one entry per detected value disagreement.
    conflicts: list[str] = field(default_factory=list)

    @property
    def has_subarea(self) -> bool:
        return self.subarea is not None

    @property
    def subarea_has_conflict(self) -> bool:
        return self.has_subarea and bool(self.subarea.alternatives)

    @property
    def has_overlay_name(self) -> bool:
        return self.overlay_name is not None

    @property
    def overlay_name_has_conflict(self) -> bool:
        return self.has_overlay_name and bool(self.overlay_name.alternatives)

    @property
    def has_specific_plan_subarea(self) -> bool:
        return self.specific_plan_subarea is not None

    @property
    def specific_plan_subarea_has_conflict(self) -> bool:
        return self.has_specific_plan_subarea and bool(self.specific_plan_subarea.alternatives)

    @property
    def has_any_conflict(self) -> bool:
        return bool(self.conflicts)

    @property
    def narrowing_strength(self) -> str:
        """How effectively this context can narrow a document search.

        Returns 'strong', 'moderate', 'weak', or 'none'.

        Scoring:
          subarea (CPIO)         = 3 pts  — best narrowing signal for CPIO
          overlay_name           = 2 pts
          specific_plan_subarea  = 2 pts  — useful but for a different doc type
          ordinance_number       = 1 pt
        """
        score = 0
        if self.has_subarea:
            score += 3
        if self.has_overlay_name:
            score += 2
        if self.has_specific_plan_subarea:
            score += 2
        if self.ordinance_number is not None:
            score += 1
        if score >= 5:
            return "strong"
        if score >= 3:
            return "moderate"
        if score >= 1:
            return "weak"
        return "none"


# ── Field-level set with priority and conflict recording ─────────────────────

def _set_field(
    ctx: NarrowingContext,
    field_name: str,
    value: str | None,
    source: str,
    confidence: str,
    source_detail: str = "",
) -> None:
    """Set a context field, respecting source priority.

    If the field has no value, set it directly.
    If the field already has the same value, upgrade source if higher priority.
    If the field has a different value, the higher-priority source wins as
    primary; the other is preserved as an alternative and a conflict is recorded.
    """
    if not value:
        return

    existing: NarrowingContextField | None = getattr(ctx, field_name, None)

    if existing is None:
        setattr(ctx, field_name, NarrowingContextField(
            value=value,
            source=source,
            confidence=confidence,
            source_detail=source_detail,
        ))
        return

    # Same value — upgrade source if new is higher priority, no conflict.
    if existing.value == value:
        existing_prio = _SOURCE_PRIORITY.get(existing.source, 99)
        new_prio = _SOURCE_PRIORITY.get(source, 99)
        if new_prio < existing_prio:
            existing.source = source
            existing.confidence = confidence
            existing.source_detail = source_detail
        return

    # Different values from different sources — conflict.
    existing_prio = _SOURCE_PRIORITY.get(existing.source, 99)
    new_prio = _SOURCE_PRIORITY.get(source, 99)

    if new_prio < existing_prio:
        # New source is higher priority — promote to primary.
        alt = NarrowingAlternative(
            value=existing.value,
            source=existing.source,
            confidence=existing.confidence,
            source_detail=existing.source_detail,
            reason_not_primary=(
                f"Superseded by higher-priority source '{source}' (priority {new_prio}) "
                f"over '{existing.source}' (priority {existing_prio})"
            ),
        )
        new_field = NarrowingContextField(
            value=value,
            source=source,
            confidence=confidence,
            source_detail=source_detail,
            alternatives=existing.alternatives + [alt],
        )
        setattr(ctx, field_name, new_field)
        ctx.conflicts.append(
            f"{field_name}: '{source}' says '{value}' (primary, priority {new_prio}); "
            f"'{existing.source}' said '{existing.value}' "
            f"(alternative, priority {existing_prio})"
        )
    else:
        # Existing source is higher or equal priority — keep it as primary.
        alt = NarrowingAlternative(
            value=value,
            source=source,
            confidence=confidence,
            source_detail=source_detail,
            reason_not_primary=(
                f"Lower-priority source '{source}' (priority {new_prio}) "
                f"than primary '{existing.source}' (priority {existing_prio})"
            ),
        )
        existing.alternatives.append(alt)
        ctx.conflicts.append(
            f"{field_name}: '{existing.source}' says '{existing.value}' "
            f"(primary, priority {existing_prio}); "
            f"'{source}' says '{value}' (alternative, priority {new_prio})"
        )


# ── Context builder ───────────────────────────────────────────────────────────

def build_narrowing_context(inp: ZimasLinkedDocInput) -> NarrowingContext:
    """Build a NarrowingContext from available ZimasLinkedDocInput fields.

    Sources consumed (in priority order within each field):

        subarea:
          1. inp.cpio_subarea          — caller_explicit (highest priority)

        overlay_name:
          2. inp.overlay_zones         — zimas_profile_field, each CPIO-type
                                         name in the list; first sets primary,
                                         subsequent different names are conflicts

        specific_plan_subarea:
          3. inp.specific_plan_subarea — zimas_profile_field
                                         CONTEXT PRESERVATION ONLY. No specific-
                                         plan structure extraction exists yet.
                                         Carrying this field does not improve
                                         interrupt posture or calc confidence.
                                         When specific-plan extraction is added,
                                         it should consume this field.

    Fields reserved for future passes (infrastructure ready):
        ordinance_number:
          (zi_document_header extraction; not yet wired in)

    Conflict detection: if two overlay zones in inp.overlay_zones have
    different CPIO names, both are preserved and a conflict is recorded.
    A conflict on subarea or overlay_name causes downstream branch selection
    confidence to be capped at 'moderate'.
    """
    ctx = NarrowingContext()

    # ── subarea ───────────────────────────────────────────────────────
    if inp.cpio_subarea:
        _set_field(
            ctx, "subarea",
            inp.cpio_subarea,
            SOURCE_CALLER_EXPLICIT, "high",
            source_detail="ZimasLinkedDocInput.cpio_subarea",
        )

    # ── overlay_name ──────────────────────────────────────────────────
    # Each CPIO-type name in overlay_zones contributes.
    # Multiple different names produce a conflict (ambiguous which CPIO governs).
    for zone in inp.overlay_zones:
        if "cpio" in zone.lower():
            _set_field(
                ctx, "overlay_name",
                zone,
                SOURCE_ZIMAS_PROFILE_FIELD, "high",
                source_detail="ZimasLinkedDocInput.overlay_zones",
            )

    # ── specific_plan_subarea ─────────────────────────────────────────
    # Context preservation only — not consumed by any current extractor.
    # Carries the specific plan subdistrict/subarea with provenance so that
    # future specific-plan structure extraction can use it without needing
    # additional input fields or pipeline changes.
    if inp.specific_plan_subarea:
        _set_field(
            ctx, "specific_plan_subarea",
            inp.specific_plan_subarea,
            SOURCE_ZIMAS_PROFILE_FIELD, "high",
            source_detail="ZimasLinkedDocInput.specific_plan_subarea",
        )

    return ctx
