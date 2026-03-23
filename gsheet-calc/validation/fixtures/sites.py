"""Synthetic test site fixtures."""

from models.site import Site


def simple_r3_site() -> Site:
    """Simple R3 site: no overlays, no alley, Chapter 1."""
    return Site(
        address="123 Test St, Los Angeles, CA 90001",
        apn="1234-567-890",
        coordinates=(34.0522, -118.2437),
        zoning_string_raw="R3-1",
        zone="R3",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="Medium Residential",
        community_plan_area="Southeast Los Angeles",
        lot_area_sf=7500.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def r4_alley_site() -> Site:
    """R4 site with alley credit, Chapter 1."""
    return Site(
        address="456 Main St, Los Angeles, CA 90012",
        apn="2345-678-901",
        coordinates=(34.0580, -118.2480),
        zoning_string_raw="R4-1",
        zone="R4",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="High Medium Residential",
        community_plan_area="Central City North",
        lot_area_sf=6000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def c2_residential_site() -> Site:
    """C2 site for residential use (buildable = lot area exception)."""
    return Site(
        address="789 Broadway, Los Angeles, CA 90014",
        apn="3456-789-012",
        coordinates=(34.0450, -118.2500),
        zoning_string_raw="C2-2",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="2",
        general_plan_land_use="Community Commercial",
        community_plan_area="Central City",
        lot_area_sf=10000.0,
        toc_tier=3,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def area_mismatch_site() -> Site:
    """Site with assessor vs survey lot area mismatch."""
    return Site(
        address="321 Elm Ave, Los Angeles, CA 90003",
        apn="4567-890-123",
        coordinates=(34.0400, -118.2600),
        zoning_string_raw="R3-1",
        zone="R3",
        zone_code_chapter="chapter_1",
        height_district="1",
        lot_area_sf=7500.0,
        survey_lot_area_sf=7380.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def multiple_parcel_site() -> Site:
    """Site with multiple parcels."""
    return Site(
        address="555 Spring St, Los Angeles, CA 90013",
        apn="5678-901-234",
        coordinates=(34.0500, -118.2450),
        zoning_string_raw="R4-2",
        zone="R4",
        zone_code_chapter="chapter_1",
        height_district="2",
        lot_area_sf=15000.0,
        multiple_parcels=True,
        parcel_count=2,
        lot_tie_assumed=True,
        chapter_applicability_confidence="high",
        parcel_match_confidence="low",
    )


def chapter_unknown_site() -> Site:
    """Site where Chapter 1 vs 1A cannot be determined."""
    return Site(
        address="999 Wilshire Blvd, Los Angeles, CA 90017",
        apn="6789-012-345",
        coordinates=(34.0530, -118.2580),
        zoning_string_raw="C2-2",
        zone="C2",
        zone_code_chapter="unknown",
        height_district="2",
        lot_area_sf=8000.0,
        chapter_applicability_confidence="unknown",
        parcel_match_confidence="high",
    )


def corner_lot_site() -> Site:
    """Corner lot with dedications."""
    return Site(
        address="100 Corner Ave, Los Angeles, CA 90005",
        apn="7890-123-456",
        coordinates=(34.0600, -118.2700),
        zoning_string_raw="RAS4-1VL",
        zone="RAS4",
        zone_code_chapter="chapter_1",
        height_district="1-VL",
        lot_area_sf=9000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def accessible_parking_site() -> Site:
    """Site for testing accessible parking with commercial component."""
    return Site(
        address="200 Commerce Blvd, Los Angeles, CA 90015",
        apn="8901-234-567",
        coordinates=(34.0470, -118.2550),
        zoning_string_raw="C2-1",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1",
        lot_area_sf=12000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


# ── FAR-specific test fixtures ──────────────────────────────────────────


def far_c2_1_no_overrides() -> Site:
    """Test Case 1: Standard C2-1, no overrides. (417 Alvarado pattern, baseline only)."""
    return Site(
        address="417 Alvarado St, Los Angeles, CA 90057",
        apn="5154-031-006",
        zoning_string_raw="C2-1",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="Community Commercial",
        community_plan_area="Westlake",
        lot_area_sf=22495.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_c2_2d_cpio_tcc_beacon() -> Site:
    """Test Case 2: C2-2D-CPIO (TCC Beacon pattern). D and CPIO present, docs not parsed."""
    return Site(
        address="155 W 6th St, San Pedro, CA 90731",
        apn="7449-020-001",
        zoning_string_raw="C2-2D-CPIO",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="2",
        general_plan_land_use="Regional Commercial",
        community_plan_area="San Pedro",
        lot_area_sf=56341.0,
        d_limitations=["Ord-XXXXX"],
        overlay_zones=["San Pedro CPIO"],
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_c2_2d_cpio_327_harbor() -> Site:
    """Test Case 3: C2-2D-CPIO with explicit CPIO FAR (327 North Harbor pattern).

    CPIO Ordinance #185539 Subarea E: max FAR 4.0:1, area basis = lot area.
    """
    return Site(
        address="327 North Harbor Blvd, San Pedro, CA 90731",
        apn="7449-014-013",
        zoning_string_raw="C2-2D-CPIO",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="2",
        general_plan_land_use="Community Commercial",
        community_plan_area="San Pedro",
        lot_area_sf=24197.0,
        survey_lot_area_sf=24197.0,
        multiple_parcels=True,
        parcel_count=2,
        d_limitations=["Ord-185539"],
        overlay_zones=["San Pedro CPIO"],
        specific_plan_subarea="E",
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_c2_1_density_bonus() -> Site:
    """Test Case 4: C2-1 with Density Bonus (417 Alvarado full pattern)."""
    return Site(
        address="415-421 S Alvarado St, Los Angeles, CA 90057",
        apn="5154-031-006",
        zoning_string_raw="C2-1",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="Community Commercial",
        community_plan_area="Westlake",
        lot_area_sf=22495.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_r4_1_simple() -> Site:
    """Test Case 5: Simple R4-1 residential, no overlays."""
    return Site(
        address="456 Main St, Los Angeles, CA 90012",
        apn="2345-678-901",
        zoning_string_raw="R4-1",
        zone="R4",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="High Medium Residential",
        community_plan_area="Central City North",
        lot_area_sf=10000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_c2_1vl_cpio_missing_doc() -> Site:
    """Test Case 6: C2-1VL-CPIO, CPIO document not available."""
    return Site(
        address="100 Test Blvd, Los Angeles, CA 90001",
        apn="9999-001-001",
        zoning_string_raw="C2-1VL-CPIO",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1VL",
        overlay_zones=["Unknown CPIO"],
        lot_area_sf=15000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def far_multi_parcel_lot_tie() -> Site:
    """Test Case 7: Multiple parcels, lot tie not confirmed."""
    return Site(
        address="327 North Harbor Blvd, San Pedro, CA 90731",
        apn="7449-014-013",
        zoning_string_raw="C2-2D-CPIO",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="2",
        lot_area_sf=24197.0,
        multiple_parcels=True,
        parcel_count=2,
        lot_tie_assumed=False,
        d_limitations=["Ord-185539"],
        overlay_zones=["San Pedro CPIO"],
        chapter_applicability_confidence="high",
        parcel_match_confidence="low",
    )
