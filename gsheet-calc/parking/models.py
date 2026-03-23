"""Pydantic models for parking module structured output."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParkingIssue(BaseModel):
    module: str = "parking"
    step: str
    field: str
    severity: str = "warning"  # warning / error / info
    message: str
    action_required: str = ""
    confidence_impact: str = ""  # degrades_to_provisional / degrades_to_unresolved / none


class CodeFamily(BaseModel):
    chapter: str = "1"  # 1 / 1A / unresolved
    basis: str = "default_ch1"  # default_ch1 / confirmed_filing_date / user_override
    confidence: str = "confirmed"  # confirmed / provisional


class AB2097Result(BaseModel):
    eligible: bool | None = None
    transit_type: str | None = None  # rail / brt / bus_intersection / none
    distance_to_stop: float | None = None
    stop_name: str | None = None
    max_parking_if_eligible: str = "per_AB_2097_at_filing"
    # Whether project-side use exclusions were evaluated (hotel/motel/transient, etc.)
    project_use_exclusions_checked: bool = False
    project_use_exclusion_found: bool | None = None  # None = not checked, True = disqualifying use found
    confidence: str = "unresolved"  # confirmed / provisional / unresolved
    issues: list[ParkingIssue] = Field(default_factory=list)


class UnitParkingLine(BaseModel):
    unit_type: str
    count: int
    hab_rooms: int
    rate: float
    spaces: float


class CommercialParkingLine(BaseModel):
    use: str
    area_sf: float
    rate: float
    per_unit_sf: float
    spaces: float


class BaselineParking(BaseModel):
    residential_by_unit_type: list[UnitParkingLine] = Field(default_factory=list)
    residential_total: float = 0.0
    guest_rooms: int | None = None
    guest_parking: float | None = None
    commercial_uses: list[CommercialParkingLine] | None = None
    commercial_total: float | None = None
    total_baseline: float = 0.0
    hab_rooms_source: str = "actual"  # actual / converted_from_bedrooms
    # True when no unit mix was provided and a fallback assumption was used
    used_default_unit_mix_assumption: bool = False
    # "confirmed" / "provisional" / "unresolved" — tracks commercial mapping quality
    commercial_mapping_confidence: str = "confirmed"
    # Rounding convention applied to total_baseline
    total_rounding_convention: str = "ceil_residential_plus_commercial"
    status: str = "provisional"  # confirmed / provisional
    issues: list[ParkingIssue] = Field(default_factory=list)


class FlaggedControl(BaseModel):
    """A site control detected but not fully interpreted for parking impact."""
    control_type: str  # specific_plan / cpio / d_limitation / q_condition
    identifier: str  # plan name, ordinance number, overlay name
    may_affect_parking: bool | None = None  # None = unknown, True = likely, False = confirmed no
    document_status: str = "not_reviewed"  # not_reviewed / reviewed_no_impact / reviewed_has_impact


class LaneGatingSignals(BaseModel):
    """Structured signals for downstream lane-selection logic.

    These do NOT decide the operative lane. They indicate plausibility
    and blockers so downstream logic is not guessing in a vacuum.
    """
    code_family_resolved: bool = False
    ab2097_plausible: bool | None = None  # None = insufficient data
    toc_plausible: bool | None = None
    state_db_plausible: bool | None = None
    # Site controls that may block confident lane selection
    unresolved_controls_that_may_affect_lanes: list[str] = Field(default_factory=list)


class ParkingInterrupters(BaseModel):
    specific_plan_overrides_parking: bool | None = None
    overlay_overrides_parking: bool | None = None
    d_q_affects_parking: bool | None = None
    # None = not checked / unknown; True = found; False = checked and none found
    prior_case_conditions: bool | None = None
    mpr_district: bool | None = None
    # Structured list of detected-but-not-interpreted controls
    flagged_controls: list[FlaggedControl] = Field(default_factory=list)
    # Baseline local: always LAMC 12.21 A.4 unless a site-specific control is confirmed to replace it
    baseline_local_parking_source: str = "LAMC 12.21 A.4"
    # Governing: only populated when no unresolved interrupter could affect parking authority
    governing_parking_source: str | None = None
    # Lane gating signals for downstream
    lane_gating: LaneGatingSignals = Field(default_factory=LaneGatingSignals)
    confidence: str = "provisional"  # confirmed / provisional / unresolved
    issues: list[ParkingIssue] = Field(default_factory=list)


class ParkingLane(BaseModel):
    selected: str = "unresolved"  # none / ab2097 / toc / state_db / unresolved
    # Why this lane was selected (traceable rationale)
    selection_basis: str = ""  # user_selected / density_lane_aligned / unresolved
    ab2097_result: float | None = None
    toc_result: float | None = None
    state_db_result: float | None = None
    governing_minimum: float | None = None
    governing_source: str | None = None
    confidence: str = "unresolved"  # confirmed / provisional / unresolved


class TOCParking(BaseModel):
    tier: int | None = None
    tier_verified: bool = False
    rate_per_unit: float | None = None
    total_units: int | None = None
    required_spaces: float | None = None
    # Project plans/intends 100% affordable
    is_100_pct_affordable: bool = False
    # Explicit confirmation that 100% affordable treatment governs TOC parking
    is_100_pct_affordable_confirmed: bool = False
    # Rounding convention applied
    total_rounding_convention: str = "ceil_units_times_rate"
    # Implementation scope disclosure
    implemented_branch: str = ""  # e.g. "standard_0.5_per_unit" / "100pct_affordable_zero"
    branches_not_implemented: list[str] = Field(default_factory=list)
    status: str = "provisional"  # confirmed / provisional
    issues: list[ParkingIssue] = Field(default_factory=list)


class StateDBUnitParkingLine(BaseModel):
    unit_type: str
    count: int
    rate: float
    spaces: float


class StateDBParking(BaseModel):
    unit_mix: list[StateDBUnitParkingLine] = Field(default_factory=list)
    total_required: float | None = None
    statutory_section: str = "Gov. Code 65915(p)"
    # Project intends / plans 100% affordable (derived from affordability percentages)
    is_100_pct_affordable: bool = False
    # Explicit confirmation that 100% affordable treatment governs this parking calc
    is_100_pct_affordable_confirmed: bool = False
    # True when no unit mix was provided and a fallback assumption was used
    used_default_unit_mix_assumption: bool = False
    # Rounding convention applied to total_required
    total_rounding_convention: str = "ceil_per_unit_sum"
    # Which statutory branches this module actually evaluated (partial coverage disclosure)
    statutory_branches_evaluated: list[str] = Field(default_factory=list)
    statutory_branches_not_evaluated: list[str] = Field(default_factory=list)
    status: str = "provisional"  # confirmed / provisional
    issues: list[ParkingIssue] = Field(default_factory=list)


class ParkingComparison(BaseModel):
    legal_minimum: float | None = None
    proposed_parking: int | None = None
    delta: float | None = None
    above_minimum_is_owner_choice: bool | None = None


class ParkingResult(BaseModel):
    baseline_local_required_parking: float | None = None
    governing_reduced_required_parking: float | None = None
    active_parking_lane: str = "unresolved"
    proposed_parking: int | None = None
    parking_delta_from_minimum: float | None = None
    authority_chain_summary: list[str] = Field(default_factory=list)
    sources_checked: list[str] = Field(default_factory=list)
    # Detected upstream and used to degrade confidence, but not fully interpreted
    sources_flagged_not_interpreted: list[str] = Field(default_factory=list)
    sources_not_checked: list[str] = Field(default_factory=list)
    manual_review_reasons: list[str] = Field(default_factory=list)
    status: str = "unresolved"  # confirmed / provisional / overridden / unresolved / conflict
    all_issues: list[ParkingIssue] = Field(default_factory=list)


class ParkingOutput(BaseModel):
    """Complete structured parking output."""
    code_family: CodeFamily = Field(default_factory=CodeFamily)
    ab2097: AB2097Result = Field(default_factory=AB2097Result)
    baseline_parking: BaselineParking = Field(default_factory=BaselineParking)
    parking_interrupters: ParkingInterrupters = Field(default_factory=ParkingInterrupters)
    parking_lane: ParkingLane = Field(default_factory=ParkingLane)
    toc_parking: TOCParking | None = None
    state_db_parking: StateDBParking | None = None
    parking_comparison: ParkingComparison = Field(default_factory=ParkingComparison)
    parking_result: ParkingResult = Field(default_factory=ParkingResult)
