"""Document extraction context — structured parcel metadata for targeted extraction.

When a document is retrieved, this module provides a DocumentContext object that
aggregates all known parcel-specific facts from upstream pipeline stages. Document
extraction and analysis code uses this context to:

1. Target the correct district/subarea/section within multi-part documents
2. Boost matches for known identifiers (ordinance numbers, case numbers)
3. Avoid applying the wrong section, overlay branch, or control family
4. Surface conflicts when context doesn't match document structure

The context is assembled from multiple upstream sources with explicit provenance
for every field. No interpretation occurs here — only structured fact aggregation.

Usage:
    context = build_document_context(
        control=cpio_control,
        link_result=cpio_link_result,
        profile=parcel_profile,
        zi_extractions=zi_extractions,
        site=site_model,
    )
    # context now carries overlay_name, subarea, ordinance_number, etc.
    # with provenance and confidence for each
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from governing_docs.models import (
    ControlLinkResult,
    ControlType,
    LinkConfidence,
    ParcelProfileData,
    SiteControl,
    SiteControlRegistry,
)

if TYPE_CHECKING:
    from governing_docs.zi_extractor import ZIExtractionResult


class ContextFieldSource(str, Enum):
    """Where a context field value originated."""
    PARCEL_PROFILE_DIRECT = "parcel_profile_direct"
    # Structurally labeled field in ZIMAS parcel profile
    # (e.g. CPIO name from the dedicated CPIO row)

    PARCEL_PROFILE_AUTHORITY = "parcel_profile_authority"
    # From a profile authority item (ZI item, ordinance, case)

    ZI_DOCUMENT_HEADER = "zi_document_header"
    # From a ZI PDF's structured header (ORDINANCE NO., etc.)

    ZI_DOCUMENT_BODY = "zi_document_body"
    # From ZI PDF body text (weaker than header)

    MAPSERVER_IDENTIFY = "mapserver_identify"
    # From ZIMAS MapServer spatial query

    ZONING_STRING_PARSE = "zoning_string_parse"
    # Inferred from parsing the zoning string

    OVERLAY_REFERENCE = "overlay_reference"
    # From static overlay reference table

    LINKER_INFERENCE = "linker_inference"
    # Inferred by the linker (e.g. SA suffix → CPIO)

    MANUAL_ENTRY = "manual_entry"
    # Hand-entered in fixture or by user

    COMMUNITY_PLAN_INFERENCE = "community_plan_inference"
    # Inferred from community plan area name


class ContextFieldConfidence(str, Enum):
    """Confidence in a context field value."""
    HIGH = "high"        # Direct structural field or document header
    MEDIUM = "medium"    # Strong inference or pattern match
    LOW = "low"          # Weak inference, needs verification
    CONFLICTED = "conflicted"  # Multiple sources disagree


@dataclass
class ContextFieldAlternative:
    """A non-primary value for a context field, preserved for review."""
    value: str
    source: ContextFieldSource
    confidence: ContextFieldConfidence
    source_detail: str | None = None
    reason_not_primary: str | None = None
    # e.g. "Lower-priority source (linker_inference) than primary (parcel_profile_direct)"


@dataclass
class ContextField:
    """A single narrowing-context field with provenance."""
    value: str | None
    source: ContextFieldSource
    confidence: ContextFieldConfidence
    source_detail: str | None = None  # e.g. "ZI-2478 header"
    alternatives: list[ContextFieldAlternative] = field(default_factory=list)
    # Non-primary values from other sources, preserved for review


@dataclass
class DocumentContext:
    """Aggregated parcel context for targeted document extraction.

    Carries every known fact about a parcel that could narrow the search
    within a governing document. Each field has explicit provenance.

    This is document-type agnostic — the same model works for CPIO ordinances,
    D limitation ordinances, specific plans, ZI documents, etc.
    """
    # ── Identity ────────────────────────────────────────────────────
    parcel_id: str | None = None
    address: str | None = None
    pin: str | None = None

    # ── Zoning ──────────────────────────────────────────────────────
    zoning_string: ContextField | None = None
    base_zone: ContextField | None = None
    height_district: ContextField | None = None

    # ── Location ────────────────────────────────────────────────────
    community_plan_area: ContextField | None = None
    council_district: ContextField | None = None

    # ── The control this document is for ────────────────────────────
    control_type: ControlType | None = None
    control_raw_value: str | None = None

    # ── Overlay / district narrowing ────────────────────────────────
    overlay_name: ContextField | None = None
    subarea: ContextField | None = None
    specific_plan_name: ContextField | None = None
    project_area: ContextField | None = None

    # ── Document identity ───────────────────────────────────────────
    ordinance_number: ContextField | None = None
    zi_code: ContextField | None = None
    case_numbers: list[ContextField] = field(default_factory=list)

    # ── Search terms for targeted extraction ─────────────────────────
    # Terms that should be boosted when scanning document text
    search_boost_terms: list[str] = field(default_factory=list)
    # Terms that indicate the wrong section (negative filter)
    search_exclude_terms: list[str] = field(default_factory=list)

    # ── Warnings ────────────────────────────────────────────────────
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def has_subarea(self) -> bool:
        return self.subarea is not None and self.subarea.value is not None

    @property
    def has_overlay_name(self) -> bool:
        return self.overlay_name is not None and self.overlay_name.value is not None

    @property
    def has_ordinance(self) -> bool:
        return self.ordinance_number is not None and self.ordinance_number.value is not None

    @property
    def narrowing_strength(self) -> str:
        """How much this context can narrow document search.

        Returns 'strong', 'moderate', 'weak', or 'none'.
        """
        score = 0
        if self.has_subarea:
            score += 3  # Subarea is the strongest narrower
        if self.has_overlay_name:
            score += 2
        if self.has_ordinance:
            score += 1
        if self.specific_plan_name and self.specific_plan_name.value:
            score += 2

        if score >= 5:
            return "strong"
        elif score >= 3:
            return "moderate"
        elif score >= 1:
            return "weak"
        return "none"

    def get_section_targeting_hints(self) -> list[str]:
        """Generate hints for which sections of a document to prioritize.

        These are not regexes — they're human-readable descriptions
        that a section-matching algorithm can use.
        """
        hints: list[str] = []
        if self.has_subarea:
            hints.append(f"Look for subarea/subdistrict: '{self.subarea.value}'")
        if self.has_overlay_name:
            hints.append(f"Look for overlay/district: '{self.overlay_name.value}'")
        if self.specific_plan_name and self.specific_plan_name.value:
            hints.append(f"Look for specific plan: '{self.specific_plan_name.value}'")
        if self.control_type:
            hints.append(f"Control type: {self.control_type.value}")
        return hints


# Source priority ranking (lower number = higher priority)
_SOURCE_PRIORITY: dict[ContextFieldSource, int] = {
    ContextFieldSource.PARCEL_PROFILE_DIRECT: 1,
    ContextFieldSource.ZI_DOCUMENT_HEADER: 2,
    ContextFieldSource.PARCEL_PROFILE_AUTHORITY: 3,
    ContextFieldSource.MAPSERVER_IDENTIFY: 4,
    ContextFieldSource.LINKER_INFERENCE: 5,
    ContextFieldSource.OVERLAY_REFERENCE: 6,
    ContextFieldSource.ZONING_STRING_PARSE: 7,
    ContextFieldSource.ZI_DOCUMENT_BODY: 8,
    ContextFieldSource.COMMUNITY_PLAN_INFERENCE: 9,
    ContextFieldSource.MANUAL_ENTRY: 10,
}


def _set_context_field(
    ctx: DocumentContext,
    field_name: str,
    new_value: str | None,
    new_source: ContextFieldSource,
    new_confidence: ContextFieldConfidence,
    new_source_detail: str | None = None,
) -> None:
    """Set a context field, respecting source priority.

    If the field already has a value from a higher-priority source,
    the new value is preserved as an alternative with conflict recorded.
    If the new source is higher priority, it becomes primary and the
    old value becomes an alternative.
    """
    if not new_value:
        return

    existing: ContextField | None = getattr(ctx, field_name, None)

    if existing is None or existing.value is None:
        # No existing value — set directly
        setattr(ctx, field_name, ContextField(
            value=new_value,
            source=new_source,
            confidence=new_confidence,
            source_detail=new_source_detail,
        ))
        return

    # Same value — no value conflict, but upgrade source if new is higher priority
    if existing.value == new_value:
        existing_priority = _SOURCE_PRIORITY.get(existing.source, 99)
        new_priority = _SOURCE_PRIORITY.get(new_source, 99)
        if new_priority < existing_priority:
            existing.source = new_source
            existing.confidence = new_confidence
            existing.source_detail = new_source_detail
        return

    # Different values — determine which is primary
    existing_priority = _SOURCE_PRIORITY.get(existing.source, 99)
    new_priority = _SOURCE_PRIORITY.get(new_source, 99)

    if new_priority < existing_priority:
        # New source is higher priority — it becomes primary
        alt = ContextFieldAlternative(
            value=existing.value,
            source=existing.source,
            confidence=existing.confidence,
            source_detail=existing.source_detail,
            reason_not_primary=(
                f"Lower-priority source ({existing.source.value}) "
                f"than primary ({new_source.value})"
            ),
        )
        new_field = ContextField(
            value=new_value,
            source=new_source,
            confidence=new_confidence,
            source_detail=new_source_detail,
            alternatives=existing.alternatives + [alt],
        )
        setattr(ctx, field_name, new_field)
        ctx.conflicts.append(
            f"{field_name} conflict: {new_source.value} says '{new_value}' "
            f"(primary, priority {new_priority}), "
            f"{existing.source.value} said '{existing.value}' "
            f"(demoted, priority {existing_priority})"
        )
    else:
        # Existing source is higher or equal priority — keep it, record alternative
        alt = ContextFieldAlternative(
            value=new_value,
            source=new_source,
            confidence=new_confidence,
            source_detail=new_source_detail,
            reason_not_primary=(
                f"Lower-priority source ({new_source.value}) "
                f"than primary ({existing.source.value})"
            ),
        )
        existing.alternatives.append(alt)
        ctx.conflicts.append(
            f"{field_name} conflict: {existing.source.value} says '{existing.value}' "
            f"(primary, priority {existing_priority}), "
            f"{new_source.value} says '{new_value}' "
            f"(alternative, priority {new_priority})"
        )


def build_document_context(
    control: SiteControl | None = None,
    link_result: ControlLinkResult | None = None,
    profile: ParcelProfileData | None = None,
    zi_extractions: list[ZIExtractionResult] | None = None,
    site: object | None = None,
    parcel_id: str | None = None,
) -> DocumentContext:
    """Assemble a DocumentContext from all available upstream data.

    Follows a strict source hierarchy — higher-priority sources overwrite
    lower-priority ones. Conflicts are preserved as warnings.
    """
    ctx = DocumentContext(parcel_id=parcel_id)

    # ── From Site model (lowest priority for most fields) ───────────
    if site:
        _enrich_from_site(ctx, site)

    # ── From parcel profile ─────────────────────────────────────────
    if profile:
        _enrich_from_profile(ctx, profile)

    # ── From the control itself ─────────────────────────────────────
    if control:
        ctx.control_type = control.control_type
        ctx.control_raw_value = control.raw_value
        _set_context_field(
            ctx, "ordinance_number", control.ordinance_number,
            ContextFieldSource.LINKER_INFERENCE, ContextFieldConfidence.MEDIUM,
            new_source_detail="SiteControl.ordinance_number",
        )
        _set_context_field(
            ctx, "subarea", control.subarea,
            ContextFieldSource.LINKER_INFERENCE, ContextFieldConfidence.MEDIUM,
            new_source_detail="SiteControl.subarea",
        )

    # ── From linker results (higher priority) ───────────────────────
    if link_result:
        _enrich_from_links(ctx, link_result)

    # ── From ZI extractions (highest for ordinance numbers) ─────────
    if zi_extractions and control:
        _enrich_from_zi_extractions(ctx, zi_extractions, profile)

    # ── Build search boost terms ────────────────────────────────────
    _build_search_terms(ctx)

    return ctx


def _enrich_from_site(ctx: DocumentContext, site: object) -> None:
    """Extract context from a Site model."""
    addr = getattr(site, "address", None)
    if addr:
        ctx.address = addr

    zs = getattr(site, "zoning_string_raw", None)
    if zs:
        ctx.zoning_string = ContextField(
            value=zs,
            source=ContextFieldSource.MAPSERVER_IDENTIFY,
            confidence=ContextFieldConfidence.HIGH,
        )

    zone = getattr(site, "zone", None)
    if zone:
        ctx.base_zone = ContextField(
            value=zone,
            source=ContextFieldSource.ZONING_STRING_PARSE,
            confidence=ContextFieldConfidence.HIGH,
        )

    hd = getattr(site, "height_district", None)
    if hd:
        ctx.height_district = ContextField(
            value=hd,
            source=ContextFieldSource.ZONING_STRING_PARSE,
            confidence=ContextFieldConfidence.HIGH,
        )

    cpa = getattr(site, "community_plan_area", None)
    if cpa:
        ctx.community_plan_area = ContextField(
            value=cpa,
            source=ContextFieldSource.MAPSERVER_IDENTIFY,
            confidence=ContextFieldConfidence.HIGH,
        )

    sp = getattr(site, "specific_plan", None)
    if sp:
        ctx.specific_plan_name = ContextField(
            value=sp,
            source=ContextFieldSource.MANUAL_ENTRY,
            confidence=ContextFieldConfidence.MEDIUM,
        )


def _enrich_from_profile(ctx: DocumentContext, profile: ParcelProfileData) -> None:
    """Extract context from parcel profile data.

    Profile direct fields are high-priority (rank 1) and will override
    lower-priority sources while preserving alternatives.
    """
    if profile.address and not ctx.address:
        ctx.address = profile.address
    if profile.parcel_id and not ctx.parcel_id:
        ctx.parcel_id = profile.parcel_id

    _set_context_field(
        ctx, "zoning_string", profile.zoning_string,
        ContextFieldSource.PARCEL_PROFILE_DIRECT, ContextFieldConfidence.HIGH,
    )

    # CPIO overlay name and subarea from profile (highest priority — direct fields)
    from governing_docs.authority_links import extract_identifiers_for_control
    cpio_ids = extract_identifiers_for_control(profile, ControlType.CPIO)

    overlay_name = cpio_ids.get("overlay_full_name") or cpio_ids.get("overlay_name")
    _set_context_field(
        ctx, "overlay_name", overlay_name,
        ContextFieldSource.PARCEL_PROFILE_DIRECT, ContextFieldConfidence.HIGH,
        new_source_detail="CPIO overlay row in parcel profile",
    )

    _set_context_field(
        ctx, "subarea", cpio_ids.get("subarea"),
        ContextFieldSource.PARCEL_PROFILE_DIRECT, ContextFieldConfidence.HIGH,
        new_source_detail="CPIO_SUBAREAS field in parcel profile",
    )

    _set_context_field(
        ctx, "zi_code", cpio_ids.get("zi_code"),
        ContextFieldSource.PARCEL_PROFILE_AUTHORITY, ContextFieldConfidence.HIGH,
    )

    if profile.specific_plan and profile.specific_plan.upper() != "NONE":
        _set_context_field(
            ctx, "specific_plan_name", profile.specific_plan,
            ContextFieldSource.PARCEL_PROFILE_DIRECT, ContextFieldConfidence.HIGH,
        )


def _enrich_from_links(ctx: DocumentContext, link_result: ControlLinkResult) -> None:
    """Extract context from linker results.

    Linker inference is priority 5 — lower than profile direct (1) or
    ZI document header (2). If the profile already set a field,
    the linker's value becomes an alternative, not a replacement.
    """
    best = link_result.best_link
    if not best or best.confidence == LinkConfidence.UNLINKED:
        return

    link_conf = (
        ContextFieldConfidence.HIGH
        if best.confidence == LinkConfidence.DETERMINISTIC
        else ContextFieldConfidence.MEDIUM
    )
    link_detail = f"Linker {best.confidence.value}: {best.rationale or ''}"

    _set_context_field(
        ctx, "ordinance_number", best.ordinance_number,
        ContextFieldSource.LINKER_INFERENCE, link_conf,
        new_source_detail=link_detail,
    )

    _set_context_field(
        ctx, "overlay_name", best.overlay_name,
        ContextFieldSource.LINKER_INFERENCE, link_conf,
    )

    _set_context_field(
        ctx, "subarea", best.subarea,
        ContextFieldSource.LINKER_INFERENCE, link_conf,
    )

    _set_context_field(
        ctx, "zi_code", best.zi_code,
        ContextFieldSource.LINKER_INFERENCE, ContextFieldConfidence.HIGH,
    )


def _enrich_from_zi_extractions(
    ctx: DocumentContext,
    zi_extractions: list[ZIExtractionResult],
    profile: ParcelProfileData | None,
) -> None:
    """Extract context from ZI document extraction results.

    ZI document header is priority 2 — very high, only below profile direct.
    """
    for ext in zi_extractions:
        if ext.header_ordinance_number:
            zi_code_match = (
                ctx.zi_code
                and ctx.zi_code.value
                and ext.zi_code.replace("ZI", "") == ctx.zi_code.value.replace("ZI-", "").replace("ZI", "")
            )
            if zi_code_match:
                _set_context_field(
                    ctx, "ordinance_number", ext.header_ordinance_number,
                    ContextFieldSource.ZI_DOCUMENT_HEADER, ContextFieldConfidence.HIGH,
                    new_source_detail=f"{ext.zi_code} header: ORDINANCE NO. {ext.header_ordinance_number}",
                )


def _build_search_terms(ctx: DocumentContext) -> None:
    """Build search boost/exclude terms from context fields."""
    boost: list[str] = []

    if ctx.has_subarea:
        boost.append(ctx.subarea.value)
    if ctx.has_overlay_name:
        boost.append(ctx.overlay_name.value)
    if ctx.has_ordinance:
        boost.append(ctx.ordinance_number.value)
    if ctx.community_plan_area and ctx.community_plan_area.value:
        boost.append(ctx.community_plan_area.value)
    if ctx.specific_plan_name and ctx.specific_plan_name.value:
        boost.append(ctx.specific_plan_name.value)
    if ctx.base_zone and ctx.base_zone.value:
        boost.append(ctx.base_zone.value)

    ctx.search_boost_terms = [t for t in boost if t]
