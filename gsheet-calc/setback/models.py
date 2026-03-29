"""Pydantic models for the setback/yards module.

All types are local to this module — no shared issue abstractions.
Models are defined in pipeline order: authority → yard_family →
edge_classifier → edge_calc → status/result.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Primitive shared types ───────────────────────────────────────────────────

class SetbackIssue(BaseModel):
    module: str = "setback"
    step: str
    field: str
    severity: str = "warning"          # warning / error / info
    message: str
    action_required: str = ""
    confidence_impact: str = ""        # degrades_to_provisional / degrades_to_unresolved / none


class AuthorityInterrupt(BaseModel):
    """An overlay or condition that was detected but not interpreted.

    Presence of any interrupt means governing_yard_family cannot be confirmed.
    The baseline is still available but should not be treated as governing.
    """
    source: str                        # "specific_plan" / "cpio" / "d_limitation" / "q_condition"
    reason: str
    status: str = "not_interpreted"    # not_interpreted / unresolved


class EarlyExit(BaseModel):
    triggered: bool = False
    reason: str | None = None


# ── setback_authority output ─────────────────────────────────────────────────

class SetbackAuthorityResult(BaseModel):
    """Output of resolve_setback_authority().

    baseline_yard_family: yard family derived from the zone table, regardless
        of overlays. Always set when the zone resolves successfully.

    governing_yard_family: only set when no unresolved authority interrupter
        is present AND no split condition exists. None when any overlay is
        flagged-but-not-interpreted, or when cm_split / ras_split is True.

    This mirrors the baseline_density_sf_per_du / governing_density_sf_per_du
    invariant in the density module.
    """
    code_family: str | None = None          # R3 / R4 / R5 / RD / C / M / RAS / CM
    baseline_yard_family: str | None = None # e.g. "R4" — set when zone resolves
    governing_yard_family: str | None = None # None when any interrupter present
    cm_split: bool = False                  # True for CM zone (both R3 and R4 possible)
    ras_split: bool = False                 # True for RAS3/RAS4 (ground-floor vs. above)
    governing_sections: list[str] = Field(default_factory=list)
    authority_interrupters: list[AuthorityInterrupt] = Field(default_factory=list)
    chapter_1a_applicable: bool | None = None  # True / False / None (unresolved)
    early_exit: EarlyExit = Field(default_factory=EarlyExit)
    confidence: str = "provisional"         # confirmed / provisional / unresolved
    issues: list[SetbackIssue] = Field(default_factory=list)


# ── setback_yard_family output ───────────────────────────────────────────────

class YardFormula(BaseModel):
    """Parametric yard formula with LAMC provenance.

    Never stores a pre-collapsed number. The calc module applies these
    parameters against actual project inputs to produce per-edge values.
    """
    yard_type: str                              # "side" / "rear" / "front"
    base_ft: float | None = None
    # Side yard increment: +lot_width_increment_ft for every lot_width_step_ft
    # of lot width above lot_width_threshold_ft.
    # e.g. R3/R4: +1 ft per 50 ft over 50 ft → increment=1, step=50, threshold=50
    lot_width_increment_ft: float | None = None
    lot_width_step_ft: float | None = None
    lot_width_threshold_ft: float | None = None
    # Story increment: +story_increment_ft per story above story_threshold
    story_increment_ft: float | None = None
    story_threshold: int | None = None
    # Maximum total side yard from story increments (e.g. 16 ft cap per Table 1b)
    story_increment_max_ft: float | None = None
    # Rear yard only: reduction when abutting alley
    alley_reduction_ft: float | None = None
    parametric: bool = True
    governing_section: str = ""
    notes: str = ""
    # Always True for front yard edges — prevailing setback is never auto-calculated
    prevailing_not_calculated: bool = False


class CMYardOption(BaseModel):
    """One path in the CM zone split — presented to reviewer, never auto-selected."""
    use_type_label: str         # "R3 uses" / "other residential at floor level"
    inherited_family: str       # "R3" / "R4"
    side_formula: YardFormula
    rear_formula: YardFormula
    front_formula: YardFormula
    governing_section: str


class YardFamilyResult(BaseModel):
    """Output of get_yard_family_rules().

    baseline_yard_family / governing_yard_family mirror the same distinction
    from SetbackAuthorityResult: governing is only set when no unresolved
    interrupter is present. yard_family reflects which one was actually used
    to build the formulas (governing when available, else baseline).

    When cm_split or ras_split is True, yard_family is None and the
    split-specific fields are populated instead.
    """
    yard_family: str | None = None              # active family used for formulas; None when split
    baseline_yard_family: str | None = None     # from authority_result.baseline_yard_family
    governing_yard_family: str | None = None    # from authority_result.governing_yard_family
    side_formula: YardFormula | None = None
    rear_formula: YardFormula | None = None
    front_formula: YardFormula | None = None
    # CM: both options exposed — neither is auto-selected
    cm_options: list[CMYardOption] = Field(default_factory=list)
    # RAS: separate ground-floor formula (commercial portion); None until resolved
    ras_ground_floor_formula: YardFormula | None = None
    requires_confirmation: bool = False
    status: str = "confirmed"               # confirmed / provisional / split_condition / unresolved
    issues: list[SetbackIssue] = Field(default_factory=list)


# ── setback_edge_classifier types ────────────────────────────────────────────

class EdgeInput(BaseModel):
    """Caller-supplied description of one lot line.

    edge_type valid values:
        "street"        — abuts a named or unnamed public street
        "alley"         — abuts a public alley
        "interior"      — interior lot line; rear vs. side not designated
        "interior_rear" — interior lot line explicitly designated as the rear
                          lot line by the caller (enables confirmed rear
                          classification on lots without alley access)
    """
    edge_id: str
    edge_type: str              # "street" / "alley" / "interior" / "interior_rear"
    street_name: str | None = None


class ClassifiedEdge(BaseModel):
    """Result of classifying one EdgeInput."""
    edge_id: str
    classification: str         # "front" / "side" / "rear" / "side_street_side"
    confidence: str = "confirmed"   # "confirmed" / "manual_confirm"
    manual_confirm_reason: str | None = None


# ── setback_edge_calc types ──────────────────────────────────────────────────

class AdjustmentStep(BaseModel):
    """One step in the baseline → use-adjusted → adjacency-adjusted chain."""
    adjustment: str             # short description of what changed
    reason: str
    section: str                # LAMC section that drives this adjustment
    value_ft: float | None = None


class EdgeResult(BaseModel):
    """Full per-edge setback result — never collapsed to a project-level number."""
    edge_id: str
    edge_classification: str    # "front" / "side" / "rear" / "side_street_side"
    edge_classification_confidence: str = "confirmed"
    baseline_yard_ft: float | None = None
    baseline_source: str = ""           # LAMC section for the baseline value
    use_adjusted_yard_ft: float | None = None
    use_adjustment_reason: str | None = None
    adjacency_adjusted_yard_ft: float | None = None
    adjacency_adjustment_reason: str | None = None
    # not_provided:    adjacency zone unknown for this edge → no adjustment assumed
    # checked:        adjacency zone known, adjustment evaluated
    # not_applicable: adjacency check not relevant for this edge type (front)
    adjacency_status: str = "not_provided"
    adu_override_yard_ft: float | None = None   # ADU portion only; None if not an ADU edge
    governing_yard_ft: float | None = None
    governing_source: str = ""
    adjustment_chain: list[AdjustmentStep] = Field(default_factory=list)
    status: str = "unresolved"  # confirmed / provisional / overridden / unresolved / split_condition
    manual_review_reasons: list[str] = Field(default_factory=list)
    prevailing_setback_flag: bool = False   # True for all front edges, always
    issues: list[SetbackIssue] = Field(default_factory=list)


# ── Orchestrator input ───────────────────────────────────────────────────────

class SetbackProjectInputs(BaseModel):
    """Project-level inputs consumed by the setback orchestrator."""
    lot_type: str = "interior"              # interior / corner / through / flag
    lot_geometry_regular: bool = True
    edges: list[EdgeInput] = Field(default_factory=list)
    use_mix: list[str] = Field(default_factory=list)        # e.g. ["residential", "retail"]
    lowest_residential_story: int | None = None             # 1 = ground floor
    ground_floor_commercial: bool = False
    adu_present: bool = False
    adu_edge_ids: list[str] = Field(default_factory=list)   # edges where ADU setback governs
    number_of_stories: int | None = None
    building_height: float | None = None
    lot_width: float | None = None
    lot_depth: float | None = None
    # edge_id → adjacency zone string, or None if unknown for that edge
    per_edge_adjacency: dict[str, str | None] = Field(default_factory=dict)
    small_lot_subdivision: bool = False
    chapter_1a_applicable: bool | None = None


# ── Pipeline state ───────────────────────────────────────────────────────────

class SetbackOutput(BaseModel):
    """Internal pipeline state assembled by the orchestrator."""
    inputs: SetbackProjectInputs = Field(default_factory=SetbackProjectInputs)
    authority_result: SetbackAuthorityResult = Field(default_factory=SetbackAuthorityResult)
    yard_family_result: YardFamilyResult | None = None
    classified_edges: list[ClassifiedEdge] = Field(default_factory=list)
    edge_results: list[EdgeResult] = Field(default_factory=list)


# ── Final output ─────────────────────────────────────────────────────────────

class SetbackResult(BaseModel):
    """Final setback module output per the sprint spec schema."""
    edges: list[EdgeResult] = Field(default_factory=list)
    overall_status: str = "unresolved"
    authority_chain_summary: list[str] = Field(default_factory=list)
    code_family: str | None = None
    inherited_yard_family: str | None = None    # governing family when resolved; None if split
    sources_checked: list[str] = Field(default_factory=list)
    sources_flagged_not_interpreted: list[str] = Field(default_factory=list)
    sources_not_checked: list[str] = Field(default_factory=list)
    manual_review_reasons: list[str] = Field(default_factory=list)
    early_exit: EarlyExit = Field(default_factory=EarlyExit)
    all_issues: list[SetbackIssue] = Field(default_factory=list)
