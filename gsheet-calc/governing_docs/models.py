"""Data models for site controls discovery, registry, and resolution.

These models are intentionally separate from existing calc-module models.
They describe *what controls exist on a parcel*, *where we learned about them*,
and *how far we got in resolving their governing documents* —
not what those controls mean for density/FAR/parking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ControlType(str, Enum):
    """Control families discoverable in Phase 1.

    Kept narrow to the highest-value families per spec.
    """
    D_LIMITATION = "d_limitation"
    Q_CONDITION = "q_condition"
    CPIO = "cpio"
    SPECIFIC_PLAN = "specific_plan"
    T_CLASSIFICATION = "t_classification"

    # Optional — only if cleanly available
    HPOZ = "hpoz"
    SNAP = "snap"

    # Catch-all for controls discovered but not yet typed
    UNKNOWN_OVERLAY = "unknown_overlay"


class DiscoverySourceType(str, Enum):
    """Where a control was discovered from."""
    SITE_MODEL = "site_model"
    ZONING_PARSE_RESULT = "zoning_parse_result"
    RAW_ZIMAS_IDENTIFY = "raw_zimas_identify"


@dataclass
class SiteControl:
    """A single discovered site control with full provenance.

    Over-discovery is preferred: create one SiteControl per source observation.
    Deduplication happens at the registry level.
    """
    control_type: ControlType
    raw_value: str
    source_type: DiscoverySourceType
    source_detail: str  # e.g. "Site.d_limitations[0]", "ZONE_CMPLT from layer 1102"

    # Normalized identifiers (populated if we can determine them)
    normalized_name: str | None = None
    ordinance_number: str | None = None
    subarea: str | None = None

    # Parcel identity
    parcel_id: str | None = None  # APN, PIN, or coordinate key

    # Resolution status
    document_resolution_likely_required: bool = True
    resolution_notes: str | None = None

    # Provenance
    zimas_layer_id: int | None = None
    zimas_layer_name: str | None = None
    raw_field_name: str | None = None

    # Warnings
    warnings: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        """Key for deduplication across sources.

        For D_LIMITATION, Q_CONDITION, T_CLASSIFICATION: key is just the control_type,
        because a parcel has at most one of each and raw values vary across sources
        (e.g. "2D" vs "Ord-XXXXX" vs "D").

        For CPIO, SPECIFIC_PLAN, overlays: key includes normalized_name since
        multiple distinct instances are possible (though rare).
        """
        singleton_types = {
            ControlType.D_LIMITATION,
            ControlType.Q_CONDITION,
            ControlType.T_CLASSIFICATION,
        }
        if self.control_type in singleton_types:
            return f"{self.control_type.value}::_singleton"
        name_part = self.normalized_name or self.raw_value
        return f"{self.control_type.value}::{name_part}".lower().strip()


@dataclass
class RegistryConflict:
    """Records a conflict between two discovery observations of the same control."""
    dedupe_key: str
    field_name: str
    values: list[str]
    source_details: list[str]
    resolution: str = "unresolved"  # unresolved / picked_first / manual_required


@dataclass
class SiteControlRegistry:
    """Merged, deduplicated set of controls for a parcel.

    Preserves all raw evidence. Does not silently collapse conflicts.
    """
    parcel_id: str | None = None
    controls: list[SiteControl] = field(default_factory=list)
    conflicts: list[RegistryConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # All raw observations before dedup, for audit trail
    all_observations: list[SiteControl] = field(default_factory=list)

    @property
    def control_types_present(self) -> set[ControlType]:
        return {c.control_type for c in self.controls}

    def get_controls_by_type(self, ct: ControlType) -> list[SiteControl]:
        return [c for c in self.controls if c.control_type == ct]

    @property
    def has_unresolved_conflicts(self) -> bool:
        return any(c.resolution == "unresolved" for c in self.conflicts)

    @property
    def documents_likely_required(self) -> list[SiteControl]:
        return [c for c in self.controls if c.document_resolution_likely_required]


# ── Phase 2A: Resolution models ──────────────────────────────────────


class ResolutionStatus(str, Enum):
    """How far we got in resolving a control's governing document.

    Ordered from least to most resolved. Never overclaim — if we only know
    a control exists but have no identifier, that's identified_only.
    """
    IDENTIFIED_ONLY = "identified_only"
    # We know the control exists (e.g. "D" suffix in zoning string)
    # but have no ordinance number, no document, nothing actionable.

    IDENTIFIER_PARTIAL = "identifier_partial"
    # We have some identifier (e.g. an ordinance number, or a CPIO name
    # inferred from community plan) but not enough to retrieve or interpret
    # the governing document without additional lookup.

    IDENTIFIER_COMPLETE = "identifier_complete"
    # We have enough identifiers to locate the document (ordinance number
    # AND subarea for CPIOs, etc.) but have NOT retrieved or parsed it.
    # This is the ceiling for Phase 2A — no document fetching.

    DOCUMENT_REQUIRED = "document_required"
    # Identifiers exist but document must be fetched and parsed to determine
    # what development standards the control imposes. This is the typical
    # final state for Phase 2A.

    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    # Resolution cannot proceed without human intervention — e.g. conflicting
    # identifiers, placeholder ordinance numbers, ambiguous format.


@dataclass
class ControlResolution:
    """Resolution status for a single site control.

    Wraps a SiteControl with structured resolution state, what's known,
    what's missing, and actionable next steps.
    """
    control: SiteControl
    status: ResolutionStatus

    # What we were able to determine
    inferred_name: str | None = None  # e.g. "San Pedro CPIO" inferred from community plan
    ordinance_number: str | None = None
    subarea: str | None = None

    # What's missing for full resolution
    missing: list[str] = field(default_factory=list)
    # e.g. ["ordinance_number", "subarea", "document_text"]

    # Actionable next step
    next_step: str | None = None
    # e.g. "Look up D ordinance number via ZIMAS case search for this APN"

    # Quality flags
    identifier_is_placeholder: bool = False
    source_format_unreliable: bool = False
    source_format_warning: str | None = None

    # Warnings accumulated during resolution
    warnings: list[str] = field(default_factory=list)


@dataclass
class RegistryResolution:
    """Resolution results for an entire SiteControlRegistry."""
    parcel_id: str | None = None
    resolutions: list[ControlResolution] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_statuses(self) -> list[ResolutionStatus]:
        return [r.status for r in self.resolutions]

    @property
    def worst_status(self) -> ResolutionStatus | None:
        if not self.resolutions:
            return None
        # ResolutionStatus is ordered least→most resolved, so min is worst
        priority = list(ResolutionStatus)
        return min(self.all_statuses, key=lambda s: priority.index(s))

    def get_by_type(self, ct: ControlType) -> list[ControlResolution]:
        return [r for r in self.resolutions if r.control.control_type == ct]

    @property
    def needs_manual_review(self) -> bool:
        return any(
            r.status == ResolutionStatus.MANUAL_REVIEW_REQUIRED
            for r in self.resolutions
        )

    @property
    def has_actionable_next_steps(self) -> bool:
        return any(r.next_step for r in self.resolutions)


# ── Control-to-authority linking models ──────────────────────────────


class LinkConfidence(str, Enum):
    """How confident the link between a control and an authority item is.

    Ordered from strongest to weakest.
    """
    DETERMINISTIC = "deterministic"
    # Profile has a structurally labeled field that maps 1:1 to this control.
    # Example: the dedicated "CPIO: San Pedro" row in the zoning tab.

    PROBABLE = "probable"
    # Strong pattern-based evidence but not structurally labeled.
    # Example: ORD-185541-SA135 on a CPIO parcel → likely CPIO ordinance (SA = subarea).

    CANDIDATE_SET = "candidate_set"
    # Multiple items could match; we list them all without picking one.
    # Example: 5 ordinances on a D-limitation parcel; one is the D ordinance.

    UNLINKED = "unlinked"
    # No profile items match this control at all.


@dataclass
class AuthorityLink:
    """A proposed link between a SiteControl and one or more ParcelAuthorityItems."""
    control_type: ControlType
    confidence: LinkConfidence

    # The linked item(s)
    linked_items: list[ParcelAuthorityItem] = field(default_factory=list)

    # For deterministic/probable: the single best match
    primary_item: ParcelAuthorityItem | None = None

    # Extracted identifiers from the link
    ordinance_number: str | None = None
    zi_code: str | None = None
    overlay_name: str | None = None
    subarea: str | None = None

    # Why this link was made
    rationale: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ControlLinkResult:
    """Full linking result for one SiteControl."""
    control: SiteControl
    links: list[AuthorityLink] = field(default_factory=list)

    @property
    def best_link(self) -> AuthorityLink | None:
        if not self.links:
            return None
        priority = list(LinkConfidence)
        return min(self.links, key=lambda l: priority.index(l.confidence))

    @property
    def best_confidence(self) -> LinkConfidence:
        if not self.links:
            return LinkConfidence.UNLINKED
        return self.best_link.confidence


# ── Phase 2B: Authority-link models ─────────────────────────────────


class AuthorityLinkType(str, Enum):
    """Classification of a linked authority item from ZIMAS parcel profile.

    Distinct from ControlType — this describes what kind of *link* it is,
    not what control family it belongs to.
    """
    ZONING_INFORMATION = "zoning_information"  # ZI-NNNN items
    ORDINANCE = "ordinance"                    # Ord #NNNNNN
    PLANNING_CASE = "planning_case"            # CPC-YYYY-NNNNN-XXX
    DIR_DETERMINATION = "dir_determination"    # DIR-YYYY-NNNNN-XXX
    OVERLAY_DISTRICT = "overlay_district"      # Named overlay (e.g. CPIO full name)
    SPECIFIC_PLAN_REF = "specific_plan_ref"    # Specific plan name
    UNKNOWN = "unknown"


class SourceTier(str, Enum):
    """Data source hierarchy for identifier resolution.

    Ordered from most authoritative / highest value to least.
    """
    ZIMAS_PARCEL_PROFILE = "zimas_parcel_profile"
    ZIMAS_MAPSERVER_IDENTIFY = "zimas_mapserver_identify"
    ZONING_STRING_PARSE = "zoning_string_parse"
    LA_CITY_PLANNING = "la_city_planning"
    CITY_CLERK_ORDINANCE = "city_clerk_ordinance"
    MANUAL_ENTRY = "manual_entry"


@dataclass
class ParcelAuthorityItem:
    """A single authority item from the ZIMAS parcel profile.

    Represents one row/link from the "Planning and Zoning" section
    of the ZIMAS parcel profile page.
    """
    raw_text: str
    link_type: AuthorityLinkType
    source_tier: SourceTier = SourceTier.ZIMAS_PARCEL_PROFILE

    # Extracted identifiers (populated by parser if detectable)
    zi_code: str | None = None          # e.g. "ZI-2478"
    zi_title: str | None = None         # e.g. "San Pedro CPO"
    ordinance_number: str | None = None # e.g. "185539"
    case_number: str | None = None      # e.g. "CPC-2009-2557-CPU"
    dir_number: str | None = None       # e.g. "DIR-2020-2595-HCA-M1"

    # Overlay-specific fields
    overlay_name: str | None = None     # e.g. "San Pedro Community Plan Implementation Overlay District"
    overlay_abbreviation: str | None = None  # e.g. "CPIO"
    subarea: str | None = None          # e.g. "E"

    # Specific plan
    specific_plan_name: str | None = None

    # Link/URL if available
    url: str | None = None

    # Which ControlType this item likely maps to (if determinable)
    mapped_control_type: ControlType | None = None

    # Confidence in the classification
    classification_confidence: str = "high"  # high / medium / low
    classification_notes: str | None = None


@dataclass
class ParcelProfileData:
    """Structured data from a ZIMAS parcel profile page.

    This is NOT currently populated by any fetcher — it's the model
    that a future parcel-profile scraper/API client would fill in.
    For now it can be constructed from known screenshot data for testing.
    """
    parcel_id: str | None = None
    address: str | None = None
    pull_timestamp: str | None = None

    # Raw profile fields
    zoning_string: str | None = None
    specific_plan: str | None = None      # "NONE" or plan name
    overlay_districts: list[str] = field(default_factory=list)
    # e.g. ["San Pedro Community Plan Implementation Overlay District (CPIO)"]

    # Structured authority items (the linked list)
    authority_items: list[ParcelAuthorityItem] = field(default_factory=list)

    # Zoning information items specifically
    zi_items: list[ParcelAuthorityItem] = field(default_factory=list)

    # Raw source URL / method for provenance
    source_url: str | None = None
    source_method: str | None = None  # "api" / "scrape" / "manual_entry"

    @property
    def has_authority_items(self) -> bool:
        return len(self.authority_items) > 0 or len(self.zi_items) > 0
