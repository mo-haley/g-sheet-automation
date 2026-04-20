from __future__ import annotations

"""Project assumptions model and supporting types."""

from pydantic import BaseModel, Field


class UnitType(BaseModel):
    label: str  # "Studio", "1BR", "2BR", "3BR"
    count: int
    habitable_rooms: int
    bedrooms: int
    avg_area_sf: float = 0.0


class OccupancyArea(BaseModel):
    occupancy_group: str  # "R-2", "S-2", "M", "B", "A-3"
    use_description: str
    area_sf: float
    floor_level: str = ""


class FloorAreaEntry(BaseModel):
    """Per-floor or per-area floor area line item for FAR numerator calculation.

    Each entry represents one row from the architect's FAR plan table.
    The `category` field determines whether this area counts toward FAR.
    """
    floor_level: str  # "1", "2", "P1", "B1", etc.
    label: str = ""  # "residential", "office and parking", "FAR", etc.
    gross_area_sf: float = 0.0
    counted_area_sf: float = 0.0  # Area counting toward FAR (per governing definition)
    excluded_area_sf: float = 0.0  # Area excluded from FAR count
    category: str = "counted"  # counted / excluded / partial
    exclusion_reason: str = ""  # e.g. "parking per LAMC 12.03", "stairways", "exterior walls"


class FrontageSegment(BaseModel):
    label: str
    length_ft: float
    open_space_width_ft: float
    qualifies: bool = False  # auto-calc: True if >= 20'


class AffordabilityPlan(BaseModel):
    eli_pct: float = 0.0
    vli_pct: float = 0.0
    li_pct: float = 0.0
    moderate_pct: float = 0.0
    market_pct: float = 0.0


class Project(BaseModel):
    project_name: str
    project_number: str = ""
    application_date: str = ""

    # G-Sheet metadata
    selected_path: str | None = None  # EntitlementPath value
    firm_name: str = ""
    issue_date: str = ""
    entitlements_text: str = ""
    legal_description: str = ""

    # Provided quantities (architect-supplied)
    open_space_provided_sf: float | None = None
    parking_auto_provided: int | None = None
    parking_accessible_provided: int | None = None
    bike_long_term_provided: int | None = None
    bike_short_term_provided: int | None = None
    ev_receptacles_provided: int | None = None
    ev_evse_provided: int | None = None

    # Site modifications
    dedication_street_ft: float = 0.0
    dedication_alley_ft: float = 0.0
    corner_cuts_sf: float = 0.0
    alley_adjacent: bool = False
    alley_width_ft: float = 0.0
    alley_frontage_length_ft: float = 0.0

    # Construction
    construction_type_podium: str | None = None
    construction_type_upper: str | None = None
    podium_levels_below_grade: int = 0
    podium_levels_above_grade: int = 0
    wood_frame_stories: int = 0
    sprinklered: bool = True

    # Program
    total_units: int = 0
    unit_mix: list[UnitType] = Field(default_factory=list)
    occupancy_areas: list[OccupancyArea] = Field(default_factory=list)

    # Floor area for FAR (numerator)
    # Option A: architect provides a single total (e.g. from FAR plan sheet)
    counted_floor_area_sf: float | None = None
    # Option B: architect provides per-floor breakdown
    floor_area_entries: list[FloorAreaEntry] = Field(default_factory=list)
    # The definition used to determine what counts (must match governing authority)
    floor_area_definition_used: str | None = None  # "LAMC 12.03" / "2020 LABC Ch.2" / CPIO-specific

    parking_spaces_total: int | None = None
    parking_assigned: int | None = None
    parking_unassigned: int | None = None
    parking_subterranean: bool | None = None

    # Accessibility (for parking calcs)
    mobility_accessible_units: int | None = None

    # Envelope
    building_perimeter_ft: float | None = None
    frontage_segments: list[FrontageSegment] = Field(default_factory=list)

    # Setbacks
    setback_front_ft: float | None = None
    setback_side_ft: float | None = None
    setback_rear_ft: float | None = None

    # Screening inputs
    project_type: str = "market_rate"
    # Explicit Track A/B gate: True = 100% affordable, False = mixed/market, None = derive from percentages
    hundred_pct_affordable: bool | None = None
    affordability: AffordabilityPlan | None = None
    prevailing_wage_committed: bool | None = None
    adaptive_reuse: bool = False
    for_sale: bool | None = None
    senior_housing: bool | None = None
    student_housing: bool | None = None
    existing_units_removed: int | None = None
    replacement_units_required: bool | None = None
    commercial_corridor_frontage: bool | None = None
