"""Pydantic models for the ZIMAS linked-document handling module.

All types are local to this module — no shared issue abstractions.
Models are defined in pipeline order:
    detected candidate → classified record → registry → fetch decision
    → confidence state → interrupt decision → orchestrator output.

Posture: unknown-first. Every record starts as detected_not_interpreted
and must be explicitly upgraded by a subsequent pipeline step.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── String constants ─────────────────────────────────────────────────────────
# Doc types
DOC_TYPE_ORDINANCE = "ordinance"
DOC_TYPE_SPECIFIC_PLAN = "specific_plan"
DOC_TYPE_OVERLAY_CPIO = "overlay_cpio"
DOC_TYPE_OVERLAY_SUPPLEMENTAL = "overlay_supplemental"
DOC_TYPE_Q_CONDITION = "q_condition"
DOC_TYPE_D_LIMITATION = "d_limitation"
DOC_TYPE_ZI_DOCUMENT = "zi_document"
DOC_TYPE_MAP_FIGURE_PACKET = "map_figure_packet"
DOC_TYPE_CASE_DOCUMENT = "case_document"
DOC_TYPE_PLANNING_PAGE = "planning_page"
DOC_TYPE_PDF_ARTIFACT = "pdf_artifact"
DOC_TYPE_UNKNOWN_ARTIFACT = "unknown_artifact"

# Usability postures
POSTURE_MACHINE_USABLE = "machine_usable"
POSTURE_MANUAL_REVIEW_FIRST = "manual_review_first"
POSTURE_CONFIDENCE_INTERRUPTER_ONLY = "confidence_interrupter_only"

# URL confidence levels
URL_CONF_DIRECT_LINK = "direct_link"       # verified direct URL to a document
URL_CONF_PORTAL_REDIRECT = "portal_redirect"  # URL goes to a portal/viewer page
URL_CONF_INFERRED = "inferred"             # constructed from pattern, not verbatim
URL_CONF_NONE = "none"                     # no URL detected

# Document confidence states
CONF_SURFACE_USABLE = "surface_usable"
CONF_FETCHED_PARTIALLY_USABLE = "fetched_partially_usable"
CONF_DETECTED_NOT_INTERPRETED = "detected_not_interpreted"
CONF_DETECTED_URL_UNVERIFIED = "detected_url_unverified"
CONF_REFUSE_TO_DECIDE = "refuse_to_decide"

# Interrupt levels (parallel to setback module confidence convention)
INTERRUPT_NONE = "none"
INTERRUPT_PROVISIONAL = "provisional"
INTERRUPT_UNRESOLVED = "unresolved"
INTERRUPT_REFUSE = "refuse_to_decide"

# Fetch decisions
FETCH_NOW = "fetch_now"
FETCH_DEFER = "defer"
FETCH_NEVER = "never"

# Detection patterns (how a candidate was found)
PATTERN_SPECIFIC_PLAN_FIELD = "specific_plan_field"
PATTERN_OVERLAY_NAME_FIELD = "overlay_name_field"
PATTERN_Q_CONDITION_FIELD = "q_condition_field"
PATTERN_D_LIMITATION_FIELD = "d_limitation_field"
PATTERN_ZI_NUMBER = "zi_number_pattern"
PATTERN_ORDINANCE_NUMBER = "ordinance_number_pattern"
PATTERN_CASE_NUMBER = "case_number_pattern"
PATTERN_URL_IN_TEXT = "url_in_text"
PATTERN_RAW_LAYER_ATTR = "raw_layer_attribute"
PATTERN_UNKNOWN = "unknown"
PATTERN_ZONE_STRING_PARSE = "zone_string_parse"

# Input coverage levels — describes how trustworthy the pipeline inputs were,
# NOT what was found. Default for unassessed runs is "partial".
INPUT_COVERAGE_COMPLETE = "complete"    # all primary sources present, parse confirmed
INPUT_COVERAGE_PARTIAL = "partial"      # some sources absent or parse quality reduced
INPUT_COVERAGE_THIN = "thin"            # most sources absent; results not reliably complete
INPUT_COVERAGE_UNCERTAIN = "uncertain"  # zone parse explicitly failed (unresolved)


# ── Issue model ───────────────────────────────────────────────────────────────

class ZimasDocIssue(BaseModel):
    """Issue raised during linked-document detection, classification, or fetch.

    Local to this module — not shared with models.issue.ReviewIssue.
    """
    module: str = "zimas_linked_docs"
    step: str
    field: str
    severity: str = "warning"          # warning / error / info
    message: str
    action_required: str = ""
    confidence_impact: str = ""        # degrades_to_provisional / degrades_to_unresolved / none


# ── link_detector output ──────────────────────────────────────────────────────

class LinkedDocCandidate(BaseModel):
    """Raw detected reference before classification.

    Produced by link_detector. One candidate per detected item.
    Candidates are not used downstream — they become LinkedDocRecords.
    A candidate with detected_pattern = "unknown" is still recorded.
    """
    candidate_id: str
    source_field: str       # which ZIMAS field or layer this came from
    raw_value: str          # the raw string from the field
    detected_pattern: str   # one of PATTERN_* constants
    url: str | None = None
    url_confidence: str = URL_CONF_NONE
    notes: str = ""
    source_ordinance_number: str | None = None  # ordinance number if known at detection time


# ── doc_classifier output ─────────────────────────────────────────────────────

class LinkedDocRecord(BaseModel):
    """Canonical record for one detected linked authority item.

    Produced by doc_classifier. One record per unique authority item.
    Default confidence state is detected_not_interpreted — this is intentional.
    All downstream steps that upgrade confidence must do so explicitly.
    """
    record_id: str
    doc_type: str           # one of DOC_TYPE_* constants
    doc_label: str          # human-readable: "Venice Specific Plan", "O-186481", "ZI-2374"
    usability_posture: str  # one of POSTURE_* constants

    # Provenance
    detected_from_fields: list[str] = Field(default_factory=list)
    raw_values: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    source_ordinance_number: str | None = None  # ordinance number from detection, before fetch

    # URL
    url: str | None = None
    url_confidence: str = URL_CONF_NONE

    # Classification confidence
    doc_type_confidence: str = "provisional"   # "confirmed" / "provisional" / "ambiguous"
    doc_type_notes: str = ""

    # Fetch state
    fetch_decision: str = FETCH_DEFER
    fetch_attempted: bool = False
    fetch_status: str = "not_attempted"        # "not_attempted" / "success" / "failed" / "skipped"
    fetch_notes: str = ""

    # Extracted surface fields (populated by structure_extractor if fetched)
    extracted_title: str | None = None
    extracted_ordinance_number: str | None = None
    extracted_chapter_list: list[str] = Field(default_factory=list)
    extracted_figure_labels: list[str] = Field(default_factory=list)
    extracted_subarea_names: list[str] = Field(default_factory=list)
    extracted_plan_name: str | None = None
    extracted_district_name: str | None = None
    extraction_notes: str = ""

    # Confidence state — starts at detected_not_interpreted, upgraded explicitly
    confidence_state: str = CONF_DETECTED_NOT_INTERPRETED

    issues: list[ZimasDocIssue] = Field(default_factory=list)


# ── doc_registry output ───────────────────────────────────────────────────────

class LinkedDocRegistry(BaseModel):
    """Registry of all linked authority items detected for a parcel.

    Produced by doc_registry. Canonical store for a single parcel run.
    Summary flags are computed by the registry builder, not caller-provided.

    registry_confidence values:
        "clean"            — no interrupters detected
        "provisional"      — q/d conditions or minor overlays only
        "has_interrupters" — specific plan, CPIO, or unresolvable items present
    """
    apn: str | None = None
    records: list[LinkedDocRecord] = Field(default_factory=list)

    # Summary flags (set by doc_registry, not caller)
    specific_plan_detected: bool = False
    cpio_detected: bool = False
    q_condition_detected: bool = False
    d_limitation_detected: bool = False
    zi_document_detected: bool = False
    case_document_detected: bool = False

    unresolved_count: int = 0       # records at detected_not_interpreted or worse
    interrupt_doc_count: int = 0    # records with posture = confidence_interrupter_only

    registry_confidence: str = "provisional"

    # Input coverage: how trustworthy was the search, independent of what was found.
    # Default is "partial" — callers must supply complete inputs to earn "complete".
    # A "clean" registry with "thin" or "uncertain" coverage is not evidence of
    # no linked authority; it is evidence of an insufficient search.
    registry_input_coverage: str = INPUT_COVERAGE_PARTIAL

    issues: list[ZimasDocIssue] = Field(default_factory=list)


# ── fetch_policy output ───────────────────────────────────────────────────────

class FetchDecision(BaseModel):
    """Fetch policy decision for one LinkedDocRecord."""
    record_id: str
    decision: str       # FETCH_NOW / FETCH_DEFER / FETCH_NEVER
    reason: str
    priority: int = 0   # lower = higher priority for fetch_now items


# ── gatekeeper output ─────────────────────────────────────────────────────────

class InterruptDecision(BaseModel):
    """Gatekeeper decision for one calc-module topic.

    Produced by gatekeeper for a specific topic (FAR, density, parking, setback).
    The calc module is responsible for acting on this — the gatekeeper does NOT
    modify any calc module output.

    blocking = True means the calc module should not produce a confirmed result.
    The calc module may still produce a provisional or unresolved skeleton for
    transparency, but must not surface a numeric answer with confidence >= medium.
    """
    topic: str                  # "FAR" / "density" / "parking" / "setback" / "height"
    interrupt_level: str        # INTERRUPT_* constant
    triggering_record_ids: list[str] = Field(default_factory=list)
    triggering_doc_labels: list[str] = Field(default_factory=list)
    reason: str = ""
    recommended_action: str = ""
    blocking: bool = False      # True when interrupt_level is UNRESOLVED or REFUSE


# ── Registry interpretation ───────────────────────────────────────────────────

class RegistryInterpretation(BaseModel):
    """Plain-language interpretation of a linked-document registry run.

    Separates two orthogonal questions that are easy to conflate:

        1. Search coverage quality  — how complete was the detection pass?
                                      Captured by: coverage_level,
                                                   may_have_undetected_authority
        2. Detected record validity — are the records that WERE found correct?
                                      Captured by: detected_records_are_valid,
                                                   records_found

    These are independent. A run can have uncertain coverage AND valid records.
    "Uncertain coverage" means the search may have missed items. It does NOT
    mean the detected records are suspect — those come from ZIMAS-verified
    field data and should be treated as present.

    The summary field states the interpretation in plain English so callers
    can surface it in logs or UI without synthesising multiple fields.
    """
    coverage_level: str
    # True when coverage is not "complete" — additional linked authority may
    # exist beyond what was detected.
    may_have_undetected_authority: bool
    # Explicit assertion: detected records come from ZIMAS-verified sources
    # (structured parcel fields, zone string parser). Coverage level does NOT
    # imply record inaccuracy. Always True in current implementation.
    detected_records_are_valid: bool = True
    records_found: int
    summary: str


# ── Orchestrator input / output ───────────────────────────────────────────────

class ZimasLinkedDocInput(BaseModel):
    """Inputs consumed by the orchestrator.

    Populated from Site model fields + optional raw ZIMAS identify response.
    Callers should pass the Site fields directly rather than the Site object
    to keep this module independent of models.site.
    """
    apn: str | None = None

    # From Site model — populated by ingest/parcel.py
    specific_plan: str | None = None
    specific_plan_subarea: str | None = None
    overlay_zones: list[str] = Field(default_factory=list)
    q_conditions: list[str] = Field(default_factory=list)
    d_limitations: list[str] = Field(default_factory=list)

    # Optional: raw ZIMAS identify response dict (provides additional layers)
    # Pass the full identify_results dict from the ArcGIS feature service.
    # Layer 105 is already handled by ingest/parcel.py — this module scans
    # other layers for linked authority signals.
    raw_zimas_identify: dict = Field(default_factory=dict)

    # Optional: raw text strings from ZIMAS portal pages or notes fields
    raw_text_fragments: list[str] = Field(default_factory=list)

    # From ZoningParseResult — richer zone string analysis
    # Populated by callers that run ingest.zoning_parser.parse_zoning_string().
    zoning_parse_confidence: str | None = None   # "confirmed" / "provisional" / "unresolved"
    zoning_parse_issues: list[str] = Field(default_factory=list)
    has_q_from_zone_string: bool = False         # [Q] bracket detected by parser
    q_ordinance_number: str | None = None        # Q ordinance number if parseable
    has_d_from_zone_string: bool = False         # [D] bracket or inline-D suffix detected by parser
    d_ordinance_number: str | None = None        # D ordinance number if parseable
    supplemental_districts_from_parse: list[str] = Field(default_factory=list)  # e.g. ["SP", "CDO"]

    # Calc topics to evaluate for interruption (defaults to all standard topics)
    topics_to_evaluate: list[str] = Field(
        default_factory=lambda: ["FAR", "density", "parking", "setback", "height"]
    )


class ZimasLinkedDocOutput(BaseModel):
    """Final output of the zimas_linked_docs orchestrator.

    Reading guide for callers:

        interpretation.summary      — plain-English synthesis; start here
        interrupt_decisions         — per-topic blocking decisions for calc modules
        registry.records            — detected linked authority items (valid regardless of coverage)
        registry_input_coverage     — how thorough was the search
        all_issues                  — ordered diagnostic issues (input_coverage first,
                                      then detection, classification, registry)

    The two questions to answer from this output:
        "What linked authority was found?" → registry.records, interrupt_decisions
        "Can I trust that the search was complete?" → registry_input_coverage,
                                                       interpretation.may_have_undetected_authority
    """
    registry: LinkedDocRegistry = Field(default_factory=LinkedDocRegistry)
    interrupt_decisions: list[InterruptDecision] = Field(default_factory=list)
    candidates_detected: int = 0
    records_classified: int = 0
    fetch_decisions: list[FetchDecision] = Field(default_factory=list)
    all_issues: list[ZimasDocIssue] = Field(default_factory=list)

    # Mirrors registry.registry_input_coverage for convenient top-level access.
    # "complete" means the search was thorough; anything else means sparse results
    # may be due to weak inputs, not genuine absence of linked authority.
    registry_input_coverage: str = INPUT_COVERAGE_PARTIAL

    # Plain-language interpretation separating coverage quality from record validity.
    # Always populated by the orchestrator. Default is a sentinel only — never
    # returned to callers; the orchestrator overwrites it before returning.
    interpretation: RegistryInterpretation = Field(
        default_factory=lambda: RegistryInterpretation(
            coverage_level=INPUT_COVERAGE_PARTIAL,
            may_have_undetected_authority=True,
            detected_records_are_valid=True,
            records_found=0,
            summary="Interpretation not yet computed.",
        )
    )
