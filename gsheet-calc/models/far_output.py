"""Structured FAR output model matching the FAR module specification.

This model captures all 10 steps of the FAR decision sequence and
maintains three separate FAR tracks (baseline, locally modified, incentive).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ParcelIdentity(BaseModel):
    address: str | None = None
    apns: list[str] = Field(default_factory=list)
    lot_area_sf: float | None = None
    survey_area_sf: float | None = None
    dedications_sf: float | None = None
    multi_parcel: bool = False
    lot_tie_confirmed: bool | None = None
    identity_confidence: str = "confirmed"  # confirmed / provisional / unresolved


class ZoningParse(BaseModel):
    raw_string: str | None = None
    base_zone: str | None = None
    zone_class: str | None = None  # residential / commercial / manufacturing / other
    height_district: str | None = None
    has_D_limitation: bool = False
    D_ordinance_number: str | None = None
    has_Q_condition: bool = False
    Q_ordinance_number: str | None = None
    has_T_classification: bool = False
    supplemental_districts: list[str] = Field(default_factory=list)
    parse_confidence: str = "confirmed"  # confirmed / provisional / unresolved


class FloorAreaDefinition(BaseModel):
    chapter: str = "unresolved"  # ch1 / ch1a / cpio_specific / unresolved
    source_citation: str = ""
    confidence: str = "low"  # high / medium / low
    note: str | None = None


class DocumentStatus(BaseModel):
    name: str
    url: str | None = None
    status: str = "not_applicable"  # downloaded_and_parsed / downloaded_not_parsed / not_available / not_applicable


class LocalControls(BaseModel):
    specific_plan: str | None = None
    specific_plan_far: float | None = None
    specific_plan_document_status: str = "not_applicable"

    cpio: str | None = None
    cpio_subarea: str | None = None
    cpio_far: float | None = None
    cpio_document_status: str = "not_applicable"

    d_limitation: bool = False
    d_ordinance: str | None = None
    d_far_cap: float | None = None
    d_document_status: str = "not_applicable"

    q_condition: bool = False
    q_ordinance: str | None = None
    q_affects_far: bool | None = None

    community_plan: str | None = None
    override_present: bool = False


class BaselineFAR(BaseModel):
    ratio: float | None = None
    source: str = ""
    zone_row_used: str = ""
    height_district_column_used: str = ""
    is_provisional: bool = False
    note: str | None = None


class IncentiveInfo(BaseModel):
    pathway: str | None = None  # density_bonus / toc / specific_plan_bonus / dir_entitlement / other
    ordinance_or_case: str | None = None
    modified_far: float | None = None
    document_status: str = "unresolved"  # confirmed / provisional / unresolved


class GoverningFAR(BaseModel):
    state: str = "unresolved"  # baseline / locally_modified / incentive_modified / unresolved
    applicable_ratio: float | None = None
    source_citation: str = ""
    confidence: str = "low"  # high / medium / low
    authority_chain: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AreaBasis(BaseModel):
    type: str = "unresolved"  # lot_area / buildable_area / net_post_dedication / survey_area / unresolved
    value_sf: float | None = None
    source: str = ""
    lot_area_sf: float | None = None
    dedications_sf: float | None = None
    buildable_area_sf: float | None = None
    confidence: str = "low"  # high / medium / low


class AllowableFloorArea(BaseModel):
    baseline_far_ratio: float | None = None
    baseline_floor_area_sf: float | None = None

    locally_modified_far_ratio: float | None = None
    locally_modified_floor_area_sf: float | None = None

    incentive_far_ratio: float | None = None
    incentive_floor_area_sf: float | None = None

    governing_floor_area_sf: float | None = None
    governing_source: str = ""


class FloorAreaBreakdownEntry(BaseModel):
    """One line from the per-floor FAR area breakdown."""
    floor_level: str
    label: str = ""
    gross_area_sf: float = 0.0
    counted_area_sf: float = 0.0
    excluded_area_sf: float = 0.0
    exclusion_reason: str = ""


class ProposedFAR(BaseModel):
    # Numerator source tracking
    numerator_source: str = "unresolved"
    # "explicit_total"    — architect provided counted_floor_area_sf directly
    # "per_floor_entries" — computed from FloorAreaEntry breakdown
    # "unresolved"        — insufficient data to determine counted floor area

    # Gross vs counted vs excluded
    gross_floor_area_sf: float | None = None
    counted_floor_area_sf: float | None = None
    excluded_floor_area_sf: float | None = None
    exclusion_breakdown: list[FloorAreaBreakdownEntry] = Field(default_factory=list)

    # Per-floor counted area (for G-sheet output)
    per_floor_breakdown: list[FloorAreaBreakdownEntry] = Field(default_factory=list)

    # Definition alignment
    floor_area_definition_used: str = ""
    definition_aligned: bool | None = None
    # True  = project's FA definition matches governing authority's definition
    # False = mismatch (e.g. project uses LABC Ch.2 but zoning requires LAMC 12.03)
    # None  = cannot determine

    # FAR calculation
    area_basis_used_sf: float | None = None
    far_ratio: float | None = None
    compliant: bool | None = None
    margin_sf: float | None = None

    # Confidence
    numerator_confidence: str = "low"  # high / medium / low
    numerator_issues: list[str] = Field(default_factory=list)


class FARIssue(BaseModel):
    step: str
    field: str
    severity: str = "info"  # warning / error / info
    message: str
    action_required: str = ""


class FAROutcome(BaseModel):
    state: str = "unresolved"
    # baseline_confirmed / baseline_with_override_risk /
    # locally_modified_confirmed / incentive_modified_confirmed / unresolved
    issues: list[str] = Field(default_factory=list)
    confidence: str = "low"  # high / medium / low
    requires_manual_review: bool = True
    manual_review_reasons: list[str] = Field(default_factory=list)


class FARMetadata(BaseModel):
    module_version: str = "far_v1.0"
    run_timestamp: str = ""
    zimas_pull_timestamp: str | None = None
    documents_checked: list[DocumentStatus] = Field(default_factory=list)


class FAROutput(BaseModel):
    """Complete structured FAR output for G-sheet integration."""
    parcel: ParcelIdentity = Field(default_factory=ParcelIdentity)
    zoning: ZoningParse = Field(default_factory=ZoningParse)
    floor_area_definition: FloorAreaDefinition = Field(default_factory=FloorAreaDefinition)
    local_controls: LocalControls = Field(default_factory=LocalControls)
    baseline_far: BaselineFAR = Field(default_factory=BaselineFAR)
    governing_far: GoverningFAR = Field(default_factory=GoverningFAR)
    area_basis: AreaBasis = Field(default_factory=AreaBasis)
    allowable: AllowableFloorArea = Field(default_factory=AllowableFloorArea)
    proposed: ProposedFAR = Field(default_factory=ProposedFAR)
    incentive: IncentiveInfo = Field(default_factory=IncentiveInfo)
    outcome: FAROutcome = Field(default_factory=FAROutcome)
    issues: list[FARIssue] = Field(default_factory=list)
    metadata: FARMetadata = Field(default_factory=FARMetadata)
