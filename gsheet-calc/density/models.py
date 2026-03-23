"""Pydantic models for density module structured output."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DensityIssue(BaseModel):
    module: str = "density"
    step: str
    field: str
    severity: str = "warning"  # warning / error / info
    message: str
    action_required: str = ""
    confidence_impact: str = ""  # degrades_to_provisional / degrades_to_unresolved / none


class ParcelRegime(BaseModel):
    base_zone: str | None = None
    height_district: str | None = None
    overlays: list[str] = Field(default_factory=list)
    specific_plan: str | None = None
    cpio: str | None = None
    cpio_subarea: str | None = None
    d_limitation: str | None = None
    q_condition: str | None = None
    general_plan_land_use: str | None = None
    toc_tier_zimas: int | None = None
    toc_tier_verified: bool = False
    near_major_transit: bool | None = None
    regime_confidence: str = "provisional"  # confirmed / provisional / unresolved


class CMCandidateOption(BaseModel):
    inherited_zone: str
    sf_per_du: int
    use_type_label: str  # e.g. "R3 uses" or "other residential at floor level"


class DensityStandard(BaseModel):
    inherited_zone: str | None = None
    sf_per_du: int | None = None
    lamc_source: str = ""
    is_provisional: bool = False
    # CM zone: both candidates exposed, sf_per_du remains None until resolved
    cm_candidate_options: list[CMCandidateOption] = Field(default_factory=list)


class GPDensityLookup(BaseModel):
    """Scaffold for General Plan land use -> density range mapping.

    Incomplete: populated entries are best-effort from GP framework.
    When a designation is not in the table, gp_density_resolved = False.
    """
    designation: str | None = None
    min_sf_per_du: int | None = None
    max_sf_per_du: int | None = None
    gp_density_resolved: bool = False
    source: str = ""


class AuthorityInterrupters(BaseModel):
    specific_plan_overrides_density: bool | None = None
    specific_plan_density_sf_per_du: int | None = None
    cpio_overrides_density: bool | None = None
    cpio_density_sf_per_du: int | None = None
    d_limitation_affects_density: bool | None = None
    q_condition_affects_density: bool | None = None
    # prior_entitlements: None = not checked, True = found, False = checked and none found
    prior_entitlements_present: bool | None = None
    gp_mismatch: bool | None = None
    gp_density_lookup: GPDensityLookup | None = None
    # baseline = always the zone-derived value (available even when governing is unknown)
    baseline_density_sf_per_du: int | None = None
    baseline_density_source: str = ""
    # governing = only populated when no unresolved interrupter could affect density
    governing_density_sf_per_du: int | None = None
    governing_density_source: str = ""
    confidence: str = "provisional"  # confirmed / provisional / unresolved
    issues: list[DensityIssue] = Field(default_factory=list)


class BaselineDensity(BaseModel):
    lot_area_used: float | None = None
    lot_area_source: str = "zimas"  # zimas / survey / post_dedication / per_specific_plan
    lot_area_basis_confidence: str = "provisional"  # confirmed / provisional / unresolved
    sf_per_du_used: int | None = None
    sf_per_du_source: str = ""
    # True when governing was available and used; False when fell back to baseline (zone-derived)
    used_governing_density: bool = False
    raw_calculation: float | None = None
    baseline_units: int | None = None
    rounding_rule_applied: str = "LAMC_12.22_A.18_floor_except_0.5_up"
    status: str = "provisional"  # confirmed / provisional / unresolved
    issues: list[DensityIssue] = Field(default_factory=list)


class IncentiveLane(BaseModel):
    selected: str = "unresolved"  # none / toc / state_db / unresolved
    ed1_pathway: bool = False
    selected_by: str = "unresolved"  # user / auto / unresolved
    confidence: str = "unresolved"  # confirmed / provisional / unresolved


class TOCDensity(BaseModel):
    tier: int | None = None
    tier_source: str = ""
    tier_verified: bool = False
    percentage_increase: float | None = None
    bonus_units: int | None = None
    total_units: int | None = None
    status: str = "provisional"  # confirmed / provisional
    issues: list[DensityIssue] = Field(default_factory=list)


class StateDBAffordableSetAside(BaseModel):
    eli_pct: float = 0.0
    vli_pct: float = 0.0
    li_pct: float = 0.0
    moderate_pct: float = 0.0


class StateDBDensity(BaseModel):
    base_density_zoning: int | None = None
    base_density_specific_plan: int | None = None
    base_density_gp: int | None = None
    # Which legs of the three-way comparison were actually evaluated
    comparison_legs_evaluated: list[str] = Field(default_factory=list)  # "zoning" / "specific_plan" / "general_plan"
    comparison_legs_unresolved: list[str] = Field(default_factory=list)
    governing_base_density: int | None = None
    governing_base_source: str = ""
    # True only when all applicable legs were evaluated; False when comparison is incomplete
    governing_base_confirmed: bool = False
    affordable_set_aside: StateDBAffordableSetAside | None = None
    bonus_percentage: float | None = None  # None means unlimited
    bonus_percentage_is_unlimited: bool = False
    bonus_units: int | None = None  # None means unlimited
    total_units: int | None = None  # None means per project proposal
    bonus_rounding_rule: str = ""  # documents which rounding rule was applied
    rental_or_for_sale: str = "unresolved"
    is_100_pct_affordable: bool = False
    # True only when 100% affordable status has been explicitly confirmed, not just derived from %
    is_100_pct_affordable_confirmed: bool = False
    statutory_authority: str = ""
    status: str = "provisional"  # confirmed / provisional / unresolved
    issues: list[DensityIssue] = Field(default_factory=list)


class EligibilityChecks(BaseModel):
    rso_replacement_required: bool | None = None
    rso_replacement_units: int | None = None
    sb330_applies: bool | None = None
    toc_affordability_met: bool | None = None
    state_db_affordability_met: bool | None = None
    no_net_loss_flag: bool | None = None
    issues: list[DensityIssue] = Field(default_factory=list)


class DensityResult(BaseModel):
    baseline_units_before_incentives: int | None = None
    claimed_density_units: int | None = None  # None if unlimited or unresolved
    claimed_density_is_unlimited: bool = False
    active_density_lane: str = "unresolved"
    ed1_pathway: bool = False
    authority_chain_summary: list[str] = Field(default_factory=list)
    sources_checked: list[str] = Field(default_factory=list)
    # Detected upstream and used to degrade confidence, but not fully interpreted
    sources_flagged_not_interpreted: list[str] = Field(default_factory=list)
    # Not checked at all
    sources_not_checked: list[str] = Field(default_factory=list)
    manual_review_reasons: list[str] = Field(default_factory=list)
    status: str = "unresolved"  # confirmed / provisional / overridden / unresolved / conflict
    all_issues: list[DensityIssue] = Field(default_factory=list)


class DensityOutput(BaseModel):
    """Complete structured density output."""
    parcel_regime: ParcelRegime = Field(default_factory=ParcelRegime)
    density_standard: DensityStandard = Field(default_factory=DensityStandard)
    authority_interrupters: AuthorityInterrupters = Field(default_factory=AuthorityInterrupters)
    baseline_density: BaselineDensity = Field(default_factory=BaselineDensity)
    incentive_lane: IncentiveLane = Field(default_factory=IncentiveLane)
    toc_density: TOCDensity | None = None
    state_db_density: StateDBDensity | None = None
    eligibility_checks: EligibilityChecks = Field(default_factory=EligibilityChecks)
    density_result: DensityResult = Field(default_factory=DensityResult)
