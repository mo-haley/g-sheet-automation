"""Tests for density and parking modules.

Covers the 5 test cases from the sprint spec plus additional unit tests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import math

from density.density_authority import (
    ZONE_DENSITY_TABLE,
    check_authority_interrupters,
    establish_parcel_regime,
    map_zone_to_density_standard,
)
from density.density_baseline_calc import _lamc_round, compute_baseline_density
from density.density_orchestrator import run_density
from density.density_toc_calc import compute_toc_density
from models.project import AffordabilityPlan, OccupancyArea, Project, UnitType
from models.site import Site
from parking.parking_orchestrator import run_parking


# ── Test Fixtures ───────────────────────────────────────────────────────


def _site_417_alvarado() -> Site:
    """Test Case 1: 417 Alvarado (C2-1, Westlake, State DB, 100% Affordable)."""
    return Site(
        address="417 S Alvarado St, Los Angeles, CA 90057",
        apn="5154-031-006",
        zoning_string_raw="C2-1",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1",
        general_plan_land_use="Community Commercial",
        community_plan_area="Westlake",
        lot_area_sf=22495.0,
        toc_tier=3,
        ab2097_area=True,
        transit_stop_type="rail",
        nearest_transit_stop_distance_ft=1200.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def _project_417_alvarado() -> Project:
    """100% affordable, 109 proposed units."""
    return Project(
        project_name="417 Alvarado",
        total_units=109,
        unit_mix=[
            UnitType(label="Studio", count=40, habitable_rooms=1, bedrooms=0, avg_area_sf=400),
            UnitType(label="1BR", count=50, habitable_rooms=2, bedrooms=1, avg_area_sf=550),
            UnitType(label="2BR", count=19, habitable_rooms=3, bedrooms=2, avg_area_sf=750),
        ],
        project_type="100_pct_affordable",
        affordability=AffordabilityPlan(
            eli_pct=30.0, vli_pct=40.0, li_pct=30.0, moderate_pct=0.0, market_pct=0.0,
        ),
        for_sale=False,
        parking_spaces_total=10,
    )


def _site_327_harbor() -> Site:
    """Test Case 2: 327 North Harbor (C2-2D-CPIO, San Pedro)."""
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
        d_limitations=["Ord-185539"],
        overlay_zones=["San Pedro CPIO"],
        specific_plan_subarea="E",
        multiple_parcels=True,
        parcel_count=2,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def _project_327_harbor() -> Project:
    """100% affordable, 47 proposed units (46 affordable + 1 manager)."""
    return Project(
        project_name="327 North Harbor",
        total_units=47,
        unit_mix=[
            UnitType(label="Studio", count=10, habitable_rooms=1, bedrooms=0, avg_area_sf=400),
            UnitType(label="1BR", count=20, habitable_rooms=2, bedrooms=1, avg_area_sf=550),
            UnitType(label="2BR", count=16, habitable_rooms=3, bedrooms=2, avg_area_sf=700),
            UnitType(label="3BR", count=1, habitable_rooms=4, bedrooms=3, avg_area_sf=950),
        ],
        project_type="100_pct_affordable",
        affordability=AffordabilityPlan(
            eli_pct=20.0, vli_pct=40.0, li_pct=40.0, moderate_pct=0.0, market_pct=0.0,
        ),
        for_sale=False,
        parking_spaces_total=48,
    )


def _site_tcc_beacon() -> Site:
    """Test Case 3: TCC Beacon (C2-2D-CPIO, San Pedro, 281 units)."""
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


def _project_tcc_beacon() -> Project:
    """281 units proposed."""
    return Project(
        project_name="TCC Beacon",
        total_units=281,
        unit_mix=[
            UnitType(label="Studio", count=80, habitable_rooms=1, bedrooms=0, avg_area_sf=400),
            UnitType(label="1BR", count=120, habitable_rooms=2, bedrooms=1, avg_area_sf=550),
            UnitType(label="2BR", count=60, habitable_rooms=3, bedrooms=2, avg_area_sf=750),
            UnitType(label="3BR", count=21, habitable_rooms=4, bedrooms=3, avg_area_sf=1000),
        ],
        parking_spaces_total=300,
    )


def _site_simple_r4() -> Site:
    """Test Case 4: Simple R4 site, no overlays, no incentives."""
    return Site(
        address="456 Test St, Los Angeles, CA 90012",
        apn="9999-999-001",
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


def _project_simple_r4() -> Project:
    """25 units, straightforward mix."""
    return Project(
        project_name="Simple R4",
        total_units=25,
        unit_mix=[
            UnitType(label="Studio", count=8, habitable_rooms=2, bedrooms=0, avg_area_sf=420),
            UnitType(label="1BR", count=12, habitable_rooms=3, bedrooms=1, avg_area_sf=620),
            UnitType(label="2BR", count=5, habitable_rooms=4, bedrooms=2, avg_area_sf=870),
        ],
        parking_spaces_total=36,
    )


def _site_unresolved_authority() -> Site:
    """Test Case 5: [Q]C2-1-CPIO, CPIO and Q not pulled."""
    return Site(
        address="100 Unresolved Blvd, Los Angeles, CA 90001",
        apn="0000-000-001",
        zoning_string_raw="[Q]C2-1-CPIO",
        zone="C2",
        zone_code_chapter="chapter_1",
        height_district="1",
        q_conditions=["Q-XXXXX"],
        overlay_zones=["Unknown CPIO"],
        lot_area_sf=15000.0,
        chapter_applicability_confidence="high",
        parcel_match_confidence="high",
    )


def _project_unresolved() -> Project:
    return Project(
        project_name="Unresolved Test",
        total_units=30,
        unit_mix=[
            UnitType(label="1BR", count=20, habitable_rooms=3, bedrooms=1, avg_area_sf=600),
            UnitType(label="2BR", count=10, habitable_rooms=4, bedrooms=2, avg_area_sf=850),
        ],
        parking_spaces_total=40,
    )


# ── DENSITY UNIT TESTS ─────────────────────────────────────────────────


def test_lamc_rounding_floor():
    """Standard case: floor down."""
    assert _lamc_round(56.3) == 56
    assert _lamc_round(56.9) == 56


def test_lamc_rounding_exactly_half():
    """LAMC exception: exactly 0.5 rounds up."""
    assert _lamc_round(56.5) == 57
    assert _lamc_round(60.5) == 61


def test_zone_density_table_c2():
    """C2 inherits R4 at 400 sf/du."""
    entry = ZONE_DENSITY_TABLE["C2"]
    assert entry["inherited_zone"] == "R4"
    assert entry["sf_per_du"] == 400


def test_zone_density_table_r3():
    """R3 at 800 sf/du."""
    entry = ZONE_DENSITY_TABLE["R3"]
    assert entry["sf_per_du"] == 800


def test_zone_density_table_cm_unresolved():
    """CM zone should have null sf_per_du (authority gap)."""
    entry = ZONE_DENSITY_TABLE["CM"]
    assert entry["sf_per_du"] is None


# ── TEST CASE 1: 417 Alvarado ─────────────────────────────────────────


def test_case1_density_baseline():
    """C2-1 with 22,495 SF: floor(22495/400) = 56 baseline units."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    output = run_density(site, project, incentive_lane="state_db", lane_selected_by="user")

    assert output.density_standard.sf_per_du == 400
    assert output.density_standard.inherited_zone == "R4"
    assert output.baseline_density.baseline_units == 56


def test_case1_state_db_unlimited():
    """100% affordable project should get unlimited density bonus."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    output = run_density(site, project, incentive_lane="state_db", lane_selected_by="user")

    assert output.state_db_density is not None
    assert output.state_db_density.is_100_pct_affordable is True
    assert output.state_db_density.bonus_percentage_is_unlimited is True
    assert output.state_db_density.statutory_authority == "AB 1287 / Gov. Code 65915(f)"


def test_case1_density_result():
    """Final density result should reflect unlimited State DB."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    output = run_density(site, project, incentive_lane="state_db", lane_selected_by="user")

    assert output.density_result.baseline_units_before_incentives == 56
    assert output.density_result.claimed_density_is_unlimited is True
    assert output.density_result.active_density_lane == "state_db"


def test_case1_parking_100_affordable():
    """Parking for 100% affordable State DB project."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    density = run_density(site, project, incentive_lane="state_db", lane_selected_by="user")
    parking = run_parking(site, project, density, parking_lane="state_db")

    assert parking.state_db_parking is not None
    assert parking.state_db_parking.is_100_pct_affordable is True
    # 100% affordable at 0.5/unit: ceil(109 * 0.5) = 55
    assert parking.state_db_parking.total_required == 55
    # AB 2097 should also be eligible (near rail)
    assert parking.ab2097.eligible is True


# ── TEST CASE 2: 327 North Harbor ─────────────────────────────────────


def test_case2_density_with_overlays():
    """C2-2D-CPIO: baseline R4 at 400 sf/du, but authority interrupters should flag D and CPIO."""
    site = _site_327_harbor()
    project = _project_327_harbor()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    # Baseline from C2 = R4 = 400 sf/du
    assert output.density_standard.sf_per_du == 400
    # floor(24197 / 400) = 60.4925 -> 60
    assert output.baseline_density.baseline_units == 60
    # Should have D and CPIO interrupters
    assert output.authority_interrupters.confidence == "provisional"
    assert len(output.authority_interrupters.issues) > 0


def test_case2_parking_baseline():
    """Baseline parking for 327 Harbor unit mix."""
    site = _site_327_harbor()
    project = _project_327_harbor()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    parking = run_parking(site, project, density)

    # 10 studios(1 hab, 1.0) + 20 1BR(2 hab, 1.0) + 16 2BR(3 hab, 1.5) + 1 3BR(4 hab, 2.0)
    # = 10 + 20 + 24 + 2 = 56
    assert parking.baseline_parking.residential_total == 56.0


# ── TEST CASE 3: TCC Beacon ───────────────────────────────────────────


def test_case3_density_c2_baseline():
    """C2-2D-CPIO: baseline should be R4 (400 sf/du). 56341/400 = 140.85 -> 140."""
    site = _site_tcc_beacon()
    project = _project_tcc_beacon()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    assert output.density_standard.sf_per_du == 400
    assert output.baseline_density.baseline_units == 140
    # Authority interrupters should flag D and CPIO
    assert output.authority_interrupters.confidence == "provisional"


def test_case3_density_r5_discrepancy_note():
    """If project claims R5 (200 sf/du = 281 units), that's a discrepancy the tool should catch.

    The base C2 zone maps to R4 (400 sf/du) = 140 units.
    Getting 281 units requires either R5 density (200 sf/du) from an override,
    or a State DB unlimited bonus. This is exactly what authority interrupters flag.
    """
    site = _site_tcc_beacon()
    project = _project_tcc_beacon()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    # Baseline should NOT be 281 (that would require R5 override or unlimited DB)
    assert output.baseline_density.baseline_units != 281
    assert output.baseline_density.baseline_units == 140
    # The D limitation or CPIO may override to R5, but since docs aren't pulled,
    # the tool should flag this as provisional
    assert output.density_result.status == "provisional"


# ── TEST CASE 4: Simple R4 ────────────────────────────────────────────


def test_case4_simple_r4_density():
    """R4-1 with 10,000 SF: floor(10000/400) = 25 units."""
    site = _site_simple_r4()
    project = _project_simple_r4()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    assert output.density_standard.sf_per_du == 400
    assert output.baseline_density.baseline_units == 25
    # No overlays, but prior entitlements not checked -> provisional (conservative)
    assert output.authority_interrupters.confidence == "provisional"
    # No SP/CPIO/D/Q interrupters, so governing is populated from zone lookup
    assert output.authority_interrupters.governing_density_sf_per_du == 400
    # Baseline always available
    assert output.authority_interrupters.baseline_density_sf_per_du == 400
    # Prior entitlements not checked -> None (honest unknown)
    assert output.authority_interrupters.prior_entitlements_present is None


def test_case4_simple_r4_parking():
    """Parking per habitable room mix, no explicit lane selected."""
    site = _site_simple_r4()
    project = _project_simple_r4()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    # Explicitly select "none" lane since State DB is plausibly computed now
    parking = run_parking(site, project, density, parking_lane="none")

    # 8 studios(2 hab, 1.0) + 12 1BR(3 hab, 1.5) + 5 2BR(4 hab, 2.0)
    # = 8 + 18 + 10 = 36
    assert parking.baseline_parking.residential_total == 36.0
    assert parking.parking_lane.selected == "none"
    assert parking.parking_lane.governing_minimum == 36.0


# ── TEST CASE 5: Unresolved Authority ─────────────────────────────────


def test_case5_unresolved_density():
    """[Q]C2-1-CPIO: density must be unresolved due to unpulled CPIO and Q."""
    site = _site_unresolved_authority()
    project = _project_unresolved()
    output = run_density(site, project, incentive_lane="unresolved")

    # Authority interrupters should flag both CPIO and Q
    assert output.authority_interrupters.confidence == "provisional"
    assert output.density_result.status == "unresolved"  # Lane is unresolved
    # Should have issues for CPIO and Q
    issue_fields = [i.field for i in output.authority_interrupters.issues]
    assert "cpio_density" in issue_fields
    assert "q_condition_density" in issue_fields


def test_case5_unresolved_parking():
    """Parking cannot be confirmed when density is unresolved."""
    site = _site_unresolved_authority()
    project = _project_unresolved()
    density = run_density(site, project, incentive_lane="unresolved")
    parking = run_parking(site, project, density)

    # Parking status should inherit density's unresolved status
    assert parking.parking_result.status in ("unresolved", "provisional")


# ── TOC DENSITY TESTS ─────────────────────────────────────────────────


def test_toc_tier3_density():
    """TOC Tier 3: 70% density increase."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    output = run_density(site, project, incentive_lane="toc", lane_selected_by="user")

    assert output.toc_density is not None
    assert output.toc_density.tier == 3
    assert output.toc_density.percentage_increase == 0.70
    # 56 baseline * 0.70 = 39.2 -> floor = 39 bonus
    assert output.toc_density.bonus_units == 39
    assert output.toc_density.total_units == 95  # 56 + 39


def test_toc_parking_reduction():
    """TOC parking for 100% affordable project: 0 spaces/unit (zero-parking path)."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    density = run_density(site, project, incentive_lane="toc", lane_selected_by="user")
    parking = run_parking(site, project, density, parking_lane="toc")

    assert parking.toc_parking is not None
    # 417 Alvarado is 100% affordable -> zero-parking path (0.0/unit)
    assert parking.toc_parking.is_100_pct_affordable is True
    assert parking.toc_parking.rate_per_unit == 0.0
    assert parking.toc_parking.required_spaces == 0


# ── PARKING UNIT TESTS ────────────────────────────────────────────────


def test_ab2097_rail_provisional():
    """Site near rail station: AB 2097 eligible but provisional (mapped data, not field-verified)."""
    site = _site_417_alvarado()
    project = _project_417_alvarado()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    parking = run_parking(site, project, density)

    assert parking.ab2097.eligible is True
    assert parking.ab2097.transit_type == "rail"
    # Provisional: mapped transit data + limited use-exclusion screening ≠ confirmed
    assert parking.ab2097.confidence == "provisional"
    # Use exclusions should have been checked (project data was provided)
    assert parking.ab2097.project_use_exclusions_checked is True
    assert parking.ab2097.project_use_exclusion_found is False


def test_ab2097_not_eligible():
    """Site without transit data should be unresolved."""
    site = _site_simple_r4()
    project = _project_simple_r4()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    parking = run_parking(site, project, density)

    assert parking.ab2097.eligible is None
    assert parking.ab2097.confidence == "unresolved"


def test_code_family_chapter1():
    """Chapter 1 should be default."""
    site = _site_simple_r4()
    project = _project_simple_r4()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    parking = run_parking(site, project, density)

    assert parking.code_family.chapter == "1"


def test_parking_inherits_density_confidence():
    """Parking result should inherit density unresolved status."""
    site = _site_unresolved_authority()
    project = _project_unresolved()
    density = run_density(site, project, incentive_lane="unresolved")
    parking = run_parking(site, project, density)

    # Density is unresolved, so parking cannot be better than unresolved/provisional
    assert parking.parking_result.status != "confirmed"


def test_parking_comparison_delta():
    """Proposed parking vs legal minimum delta when lane is explicitly none."""
    site = _site_simple_r4()
    project = _project_simple_r4()
    density = run_density(site, project, incentive_lane="none", lane_selected_by="user")
    parking = run_parking(site, project, density, parking_lane="none")

    # Proposed = 36, baseline = 36, so delta = 0
    assert parking.parking_comparison.proposed_parking == 36
    assert parking.parking_comparison.legal_minimum == 36.0
    assert parking.parking_comparison.delta == 0.0


# ── CONFIDENCE CASCADE TESTS ──────────────────────────────────────────


def test_confidence_cascade_low_upstream():
    """If authority interrupters are provisional, baseline cannot be confirmed."""
    site = _site_327_harbor()  # Has D limitation and CPIO
    project = _project_327_harbor()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    assert output.authority_interrupters.confidence == "provisional"
    assert output.baseline_density.status == "provisional"


def test_sources_tracked():
    """Density and parking should track checked and unchecked sources."""
    site = _site_327_harbor()
    project = _project_327_harbor()
    output = run_density(site, project, incentive_lane="none", lane_selected_by="user")

    assert len(output.density_result.sources_checked) > 0
    assert len(output.density_result.sources_not_checked) > 0  # D and CPIO not checked
