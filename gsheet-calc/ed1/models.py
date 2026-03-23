"""Pydantic models for ED1 screening module.

ED1 = Mayor Bass Executive Directive No. 1 (3rd Revised, July 1, 2024).

This module provides conservative screening of likely ED1 eligibility
for 100% affordable housing projects in Los Angeles. Outputs are
screening signals, not legal determinations.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class ED1Status(str, Enum):
    LIKELY_ELIGIBLE = "likely_eligible"
    POTENTIALLY_ELIGIBLE = "potentially_eligible"
    LIKELY_INELIGIBLE = "likely_ineligible"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class ED1Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EnvironmentalSiteStatus(str, Enum):
    """Tri-state for hazardous waste / oil-gas well conditions."""
    UNKNOWN = "unknown"
    NOT_PRESENT = "not_present"
    PRESENT_NOT_CLEARED = "present_not_cleared"
    CLEARED = "cleared"


class HistoricResourceStatus(str, Enum):
    UNKNOWN = "unknown"
    DESIGNATED_OR_LISTED = "designated_or_listed"
    NOT_IDENTIFIED = "not_identified"


# ── Input model ──────────────────────────────────────────────────────────────

class ED1Input(BaseModel):
    """All inputs needed for ED1 screening.

    Fields default to None (unknown) so the screener can report them as
    missing rather than silently inferring. Callers populate what they
    can from Site/Project or from explicit user inputs.
    """

    # ── Core eligibility gates ───────────────────────────────────────────
    is_100_percent_affordable: Optional[bool] = None
    requires_zone_change: Optional[bool] = None
    requires_variance: Optional[bool] = None
    requires_general_plan_amendment: Optional[bool] = None

    # ── Zone classification ──────────────────────────────────────────────
    base_zone: Optional[str] = None
    zoning_is_single_family_or_more_restrictive: Optional[bool] = None
    manufacturing_zone_disallows_multifamily: Optional[bool] = None

    # ── Residential pre-bonus capacity ───────────────────────────────────
    residential_pre_bonus_allowed_units: Optional[int] = None

    # ── Fire / hillside ──────────────────────────────────────────────────
    vhfhsz_flag: Optional[bool] = None
    hillside_area_flag: Optional[bool] = None

    # ── Environmental / site conditions ──────────────────────────────────
    hazardous_site_status: Optional[EnvironmentalSiteStatus] = None
    oil_well_site_status: Optional[EnvironmentalSiteStatus] = None

    # ── Historic resource ────────────────────────────────────────────────
    historic_resource_status: Optional[HistoricResourceStatus] = None
    # True if the full list of memo-specified plan areas / CPIOs has been
    # checked (Westwood Village SP, Central City West SP, Echo Park CDO,
    # North University Park SP, South LA CPIO §1-6.C.5.b, Southeast LA
    # CPIO §1-6.C.5.b, West Adams CPIO §6.C.5.b, San Pedro CPIO §7.C.5.b).
    # None = not checked.
    protected_plan_area_historic_check_complete: Optional[bool] = None

    # ── RSO / tenant protection ──────────────────────────────────────────
    rso_subject_site: Optional[bool] = None
    rso_total_units: Optional[int] = None
    occupied_units_within_5_years: Optional[bool] = None
    replacement_unit_trigger: Optional[bool] = None

    # ── Covenant ─────────────────────────────────────────────────────────
    public_subsidy_covenant_exception_flag: Optional[bool] = None

    # ── Zone context for constraint language ─────────────────────────────
    is_residential_zone: Optional[bool] = None
    is_commercial_zone: Optional[bool] = None
    is_residential_land_use_designation: Optional[bool] = None


# ── Output sub-models ────────────────────────────────────────────────────────

class ED1BaselineComparison(BaseModel):
    """Structured comparison of baseline (non-ED1) vs ED1 pathway."""
    review_pathway: str = ""
    entitlement_friction: str = ""
    procedural_speed: str = ""
    major_obligations: str = ""
    overall_assessment: str = ""


# ── Result model ─────────────────────────────────────────────────────────────

class ED1Result(BaseModel):
    """Complete output of ED1 screening.

    All list fields use plain-English strings so the output is directly
    user-facing without further formatting.
    """
    status: ED1Status
    confidence: ED1Confidence
    summary: str

    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    obligations: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions_used: list[str] = Field(default_factory=list)

    procedural_benefits: list[str] = Field(default_factory=list)
    incentive_constraints: list[str] = Field(default_factory=list)

    comparison_to_baseline: ED1BaselineComparison = Field(
        default_factory=ED1BaselineComparison,
    )

    source_basis: str = (
        "Mayor Bass Executive Directive No. 1 (3rd Revised), "
        "effective July 1, 2024"
    )
    screening_disclaimer: str = (
        "This is a screening result, not a legal determination. "
        "Additional confirmation is required before relying on ED1 eligibility."
    )
