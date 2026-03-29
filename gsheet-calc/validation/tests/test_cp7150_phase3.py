"""Tests for CP-7150 audit Phase 3: R2 density and parking fixes.

Covers:
  G. R2 density: max 2 DU per lot (fixed cap, not area/factor)
  H. R2 parking: flat 2 spaces/unit (not hab-room-based tiers)
  I-J. Deferred: guest room parking, covered/uncovered metadata
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.settings import DATA_DIR
from calc.density import calculate_density
from calc.parking import calculate_parking
from models.site import Site
from models.project import Project, UnitType


def _load_zone_tables():
    return json.loads((DATA_DIR / "zone_tables.json").read_text())


# ─── G. R2 density ─────────────────────────────────────────────────────────


def test_r2_density_factor_is_null():
    """R2 density_factor_sf should be null (not 2500)."""
    z = _load_zone_tables()["zones"]["R2"]
    assert z["density_factor_sf"] is None


def test_r2_max_units_per_lot():
    """R2 should have max_units_per_lot = 2."""
    z = _load_zone_tables()["zones"]["R2"]
    assert z["max_units_per_lot"] == 2


def test_r2_density_returns_2_on_small_lot():
    """R2 on a 5000 sf lot should return 2 DU (not floor(5000/2500)=2 by accident)."""
    site = Site(
        address="test", zone="R2", lot_area_sf=5000.0,
        zone_code_chapter="chapter_1",
    )
    project = Project(project_name="test")
    results, issues = calculate_density(site, project)
    density = next(r for r in results if r.name == "base_density")
    assert density.value == 2


def test_r2_density_returns_2_on_large_lot():
    """R2 on a 20000 sf lot should still return 2 DU (not floor(20000/2500)=8)."""
    site = Site(
        address="test", zone="R2", lot_area_sf=20000.0,
        zone_code_chapter="chapter_1",
    )
    project = Project(project_name="test")
    results, issues = calculate_density(site, project)
    density = next(r for r in results if r.name == "base_density")
    assert density.value == 2


def test_r2_density_no_blocking_issues():
    """R2 density should not produce blocking issues (it's a known zone)."""
    site = Site(
        address="test", zone="R2", lot_area_sf=5000.0,
        zone_code_chapter="chapter_1",
    )
    project = Project(project_name="test")
    _, issues = calculate_density(site, project)
    blocking = [i for i in issues if i.blocking]
    assert len(blocking) == 0


# ─── H. R2 parking ─────────────────────────────────────────────────────────


def test_r2_parking_flat_rate():
    """R2: 2 units should require 4 spaces (2 per unit, flat rate)."""
    site = Site(
        address="test", zone="R2", lot_area_sf=5000.0,
        zone_code_chapter="chapter_1",
    )
    project = Project(
        project_name="test",
        total_units=2,
        unit_mix=[
            UnitType(label="3BR", count=2, habitable_rooms=5, bedrooms=3, avg_area_sf=1200),
        ],
    )
    results, issues = calculate_parking(site, project)
    res_parking = next(r for r in results if r.name == "residential_parking_required")
    assert res_parking.value == 4  # 2 units x 2 spaces/unit


def test_r3_parking_still_uses_tiers():
    """R3 should still use hab-room-based tiers, not zone override."""
    site = Site(
        address="test", zone="R3", lot_area_sf=7500.0,
        zone_code_chapter="chapter_1",
    )
    project = Project(
        project_name="test",
        total_units=3,
        unit_mix=[
            UnitType(label="Studio", count=1, habitable_rooms=2, bedrooms=0, avg_area_sf=450),
            UnitType(label="1BR", count=1, habitable_rooms=3, bedrooms=1, avg_area_sf=650),
            UnitType(label="2BR", count=1, habitable_rooms=4, bedrooms=2, avg_area_sf=850),
        ],
    )
    results, _ = calculate_parking(site, project)
    res_parking = next(r for r in results if r.name == "residential_parking_required")
    # 1*1.0 + 1*1.5 + 1*2.0 = 4.5 -> ceil = 5
    assert res_parking.value == 5


# ─── I-J. Deferred items: guest room parking, covered/uncovered ────────────


def test_parking_ratios_has_zone_override_section():
    """parking_ratios.json should have residential_zone_overrides section."""
    data = json.loads((DATA_DIR / "parking_ratios.json").read_text())
    assert "residential_zone_overrides" in data
    assert "R2" in data["residential_zone_overrides"]
