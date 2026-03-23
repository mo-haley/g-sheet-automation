"""Pydantic models for the dedication_screen module (v0).

Models are defined in pipeline order: input -> per-frontage result ->
site summary -> module payload.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# -- Enums -------------------------------------------------------------------


class ScreeningStatus(str, Enum):
    NO_APPARENT_DEDICATION = "no_apparent_dedication"
    POSSIBLE_DEDICATION = "possible_dedication"
    LIKELY_DEDICATION = "likely_dedication"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class ScreeningConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNRESOLVED = "unresolved"


# -- Screening tolerance -----------------------------------------------------


class ScreeningTolerances(BaseModel):
    """Single-threshold screening tolerance.

    screening_tolerance_ft: shortfall at or below this is POSSIBLE_DEDICATION.
        Shortfall above this is LIKELY_DEDICATION.
        Reflects measurement uncertainty in user-reported apparent widths.
        Set to 0.0 for a two-tier system (no POSSIBLE band).
    """
    screening_tolerance_ft: float = 2.0


# -- Issues -------------------------------------------------------------------


class DedicationIssue(BaseModel):
    module: str = "dedication_screen"
    step: str
    field: str
    severity: str = "warning"
    message: str
    action_required: str = ""
    confidence_impact: str = ""


# -- Input --------------------------------------------------------------------


class FrontageInput(BaseModel):
    """One parcel-to-street frontage edge."""
    edge_id: str
    street_name: str
    frontage_length_ft: float | None = None
    apparent_current_half_row_ft: float | None = None
    user_override_designation: str | None = None
    user_override_standard_row_ft: float | None = None


class DedicationScreenInput(BaseModel):
    """Top-level module input."""
    parcel_apn: str | None = None
    parcel_address: str | None = None
    gross_lot_area_sf: float | None = None
    lot_type: str = "interior"
    frontages: list[FrontageInput] = Field(default_factory=list)
    tolerances: ScreeningTolerances = Field(
        default_factory=ScreeningTolerances
    )


# -- Per-frontage result ------------------------------------------------------


class FrontageResult(BaseModel):
    """Complete screening result for one frontage edge."""
    edge_id: str
    street_name: str
    frontage_length_ft: float | None = None

    # Designation
    designation_class: str | None = None
    designation_source: str = "unresolved"
    designation_confidence: ScreeningConfidence = ScreeningConfidence.UNRESOLVED

    # Standard dimensions
    standard_row_ft: float | None = None
    standard_half_row_ft: float | None = None
    standard_source: str = ""
    standard_is_range: bool = False

    # Apparent current condition
    apparent_current_half_row_ft: float | None = None
    apparent_condition_source: str = "unresolved"

    # Screening delta
    screening_shortfall_ft: float | None = None
    estimated_dedication_depth_ft: float | None = None
    estimated_dedication_area_sf: float | None = None

    # Result
    frontage_status: ScreeningStatus = ScreeningStatus.MANUAL_REVIEW_REQUIRED
    frontage_confidence: ScreeningConfidence = ScreeningConfidence.UNRESOLVED
    complexity_flags: list[str] = Field(default_factory=list)
    issues: list[DedicationIssue] = Field(default_factory=list)


# -- Site summary -------------------------------------------------------------


class SiteSummary(BaseModel):
    """Aggregate dedication screening across all frontages."""
    total_estimated_dedication_area_sf: float | None = None
    dedication_area_is_partial: bool = False
    any_dedication_likely: bool = False
    all_frontages_screened: bool = False
    frontages_requiring_manual_review: int = 0
    site_status: ScreeningStatus = ScreeningStatus.MANUAL_REVIEW_REQUIRED
    site_confidence: ScreeningConfidence = ScreeningConfidence.UNRESOLVED
    adjusted_lot_area_sf: float | None = None
    manual_review_reasons: list[str] = Field(default_factory=list)


# -- Module payload -----------------------------------------------------------


DISCLAIMER = (
    "This is a preliminary dedication risk screening based on street "
    "designation standards and user-reported apparent right-of-way "
    "conditions. It is not a survey, legal determination, or substitute "
    "for a Bureau of Engineering dedication review. Actual dedication "
    "requirements may differ. Sidewalk, parkway, and improvement "
    "obligations are not addressed by this screening."
)


class DedicationScreenPayload(BaseModel):
    """Module payload — becomes module_payload dict in ModuleResult."""
    standards_table_version: str = ""
    frontage_results: list[FrontageResult] = Field(default_factory=list)
    site_summary: SiteSummary = Field(default_factory=SiteSummary)
    disclaimer: str = DISCLAIMER
