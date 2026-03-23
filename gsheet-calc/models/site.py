from __future__ import annotations

"""Site data model and DataSource provenance model."""

from pydantic import BaseModel, Field


class DataSource(BaseModel):
    field: str
    source: str
    source_url: str | None = None
    raw_reference: str | None = None
    pull_date: str | None = None
    confidence: str = "manual_required"  # auto / auto_review / manual_required
    notes: str | None = None


class Site(BaseModel):
    address: str
    apn: str | None = None
    coordinates: tuple[float, float] | None = None

    # Zoning identifiers
    zoning_string_raw: str | None = None
    zone: str | None = None
    zone_code_chapter: str | None = None  # chapter_1 / chapter_1a / unknown
    height_district: str | None = None
    general_plan_land_use: str | None = None
    community_plan_area: str | None = None
    specific_plan: str | None = None
    specific_plan_subarea: str | None = None

    # Overlays / flags
    overlay_zones: list[str] = Field(default_factory=list)
    q_conditions: list[str] = Field(default_factory=list)
    d_limitations: list[str] = Field(default_factory=list)
    coastal_zone: bool | None = None
    hillside_area: bool | None = None
    fire_hazard_zone: str | None = None
    historic_status: str | None = None

    # Transit / screening
    toc_tier: int | None = None
    ab2097_area: bool | None = None
    nearest_transit_stop_distance_ft: float | None = None
    transit_stop_type: str | None = None

    # Parcel
    lot_area_sf: float | None = None
    survey_lot_area_sf: float | None = None
    parcel_geometry: dict | None = None
    multiple_parcels: bool = False
    parcel_count: int = 1
    lot_tie_assumed: bool = False

    # Site basis
    site_basis: str = "single_parcel_assumed"  # single_parcel_assumed / multi_parcel_user / unknown
    site_basis_note: str | None = None

    # Confidence
    chapter_applicability_confidence: str = "unknown"
    parcel_match_confidence: str = "unknown"

    # Diagnostics — visible in debug/JSON output for tracing ingest decisions
    diag_all_zone_strings: list[str] = Field(default_factory=list)
    diag_zoning_ambiguous: bool = False
    diag_zoning_layer_count: int = 0
    diag_parcel_layer_count: int = 0
    diag_identify_layers_returned: list[int] = Field(default_factory=list)

    # Provenance
    data_sources: list[DataSource] = Field(default_factory=list)
    raw_source_files: list[str] = Field(default_factory=list)
    pull_timestamp: str | None = None
