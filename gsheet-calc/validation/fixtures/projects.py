"""Synthetic test project fixtures."""

from models.project import (
    AffordabilityPlan,
    OccupancyArea,
    Project,
    UnitType,
)


def simple_r3_project() -> Project:
    """Simple R3 multifamily project."""
    return Project(
        project_name="Test R3 Multifamily",
        total_units=9,
        unit_mix=[
            UnitType(label="Studio", count=3, habitable_rooms=2, bedrooms=0, avg_area_sf=450),
            UnitType(label="1BR", count=4, habitable_rooms=3, bedrooms=1, avg_area_sf=650),
            UnitType(label="2BR", count=2, habitable_rooms=4, bedrooms=2, avg_area_sf=850),
        ],
        parking_spaces_total=13,
        parking_assigned=9,
        parking_unassigned=4,
    )


def r4_alley_project() -> Project:
    """R4 project with alley adjacency."""
    return Project(
        project_name="Test R4 Alley Project",
        total_units=15,
        unit_mix=[
            UnitType(label="Studio", count=5, habitable_rooms=2, bedrooms=0, avg_area_sf=400),
            UnitType(label="1BR", count=7, habitable_rooms=3, bedrooms=1, avg_area_sf=600),
            UnitType(label="2BR", count=3, habitable_rooms=4, bedrooms=2, avg_area_sf=800),
        ],
        alley_adjacent=True,
        alley_width_ft=20.0,
        alley_frontage_length_ft=150.0,
        parking_spaces_total=20,
        parking_assigned=15,
        parking_unassigned=5,
    )


def c2_residential_project() -> Project:
    """C2 residential project with commercial ground floor."""
    return Project(
        project_name="Test C2 Mixed-Use",
        total_units=25,
        unit_mix=[
            UnitType(label="Studio", count=8, habitable_rooms=2, bedrooms=0, avg_area_sf=420),
            UnitType(label="1BR", count=12, habitable_rooms=3, bedrooms=1, avg_area_sf=620),
            UnitType(label="2BR", count=5, habitable_rooms=4, bedrooms=2, avg_area_sf=870),
        ],
        occupancy_areas=[
            OccupancyArea(occupancy_group="M", use_description="retail", area_sf=3000, floor_level="1"),
        ],
        parking_spaces_total=40,
        parking_assigned=25,
        parking_unassigned=15,
        mobility_accessible_units=2,
    )


def corner_lot_project() -> Project:
    """Corner lot project with dedications."""
    return Project(
        project_name="Test Corner Lot",
        total_units=12,
        unit_mix=[
            UnitType(label="1BR", count=8, habitable_rooms=3, bedrooms=1, avg_area_sf=600),
            UnitType(label="2BR", count=4, habitable_rooms=4, bedrooms=2, avg_area_sf=850),
        ],
        dedication_street_ft=5.0,
        corner_cuts_sf=100.0,
        parking_spaces_total=18,
        parking_assigned=12,
        parking_unassigned=6,
    )


def accessible_parking_project() -> Project:
    """Project for testing accessible parking breakdowns."""
    return Project(
        project_name="Test Accessible Parking",
        total_units=50,
        unit_mix=[
            UnitType(label="Studio", count=15, habitable_rooms=2, bedrooms=0, avg_area_sf=400),
            UnitType(label="1BR", count=20, habitable_rooms=3, bedrooms=1, avg_area_sf=620),
            UnitType(label="2BR", count=10, habitable_rooms=4, bedrooms=2, avg_area_sf=850),
            UnitType(label="3BR", count=5, habitable_rooms=5, bedrooms=3, avg_area_sf=1100),
        ],
        occupancy_areas=[
            OccupancyArea(occupancy_group="M", use_description="retail", area_sf=5000, floor_level="1"),
            OccupancyArea(occupancy_group="B", use_description="office", area_sf=2000, floor_level="2"),
        ],
        parking_spaces_total=80,
        parking_assigned=50,
        parking_unassigned=30,
        mobility_accessible_units=3,
    )
