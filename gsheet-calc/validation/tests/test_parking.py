"""Tests for parking calculations (auto + accessible + bike + EV)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.parking import calculate_parking
from validation.fixtures.projects import (
    accessible_parking_project,
    c2_residential_project,
    simple_r3_project,
)
from validation.fixtures.sites import (
    accessible_parking_site,
    c2_residential_site,
    simple_r3_site,
)


def test_r3_residential_parking():
    """R3 project: 3 studios(1sp) + 4 1BR(1.5sp) + 2 2BR(2sp) = 3+6+4 = 13 -> ceil = 13."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_parking(site, project)

    res_park = next(r for r in results if r.name == "residential_parking_required")
    # 3*1 + 4*1.5 + 2*2 = 3 + 6 + 4 = 13
    assert res_park.value == 13


def test_c2_commercial_parking():
    """C2 project with 3000sf retail: 3000/1000 * 4 = 12 spaces."""
    site = c2_residential_site()
    project = c2_residential_project()
    results, issues = calculate_parking(site, project)

    com_park = next(r for r in results if r.name == "commercial_parking_required")
    assert com_park.value == 12  # 3000 / 1000 * 4


def test_accessible_parking_residential():
    """Accessible parking with mobility units specified."""
    site = accessible_parking_site()
    project = accessible_parking_project()
    results, issues = calculate_parking(site, project)

    res_acc = next(r for r in results if r.name == "residential_accessible_parking")
    assert res_acc.value > 0
    assert res_acc.code_section == "CBC 11B-208.2.3"


def test_accessible_parking_van():
    """Van accessible spaces should be calculated."""
    site = accessible_parking_site()
    project = accessible_parking_project()
    results, issues = calculate_parking(site, project)

    van = next(r for r in results if r.name == "van_accessible_parking")
    assert van.value >= 1  # Minimum 1
    assert van.code_section == "CBC 11B-208.2.4, 11B-502.2"


def test_accessible_evcs_unresolved():
    """Accessible EVCS should be unresolved without charging config."""
    site = accessible_parking_site()
    project = accessible_parking_project()
    results, issues = calculate_parking(site, project)

    evcs = next(r for r in results if r.name == "accessible_evcs")
    assert evcs.value is None
    assert evcs.confidence == "low"


def test_bike_parking():
    """Bike parking for >3 unit project."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_parking(site, project)

    long_term = next(r for r in results if r.name == "bike_parking_long_term")
    short_term = next(r for r in results if r.name == "bike_parking_short_term")
    assert long_term.value == 9  # 1 per unit
    assert short_term.value == 2  # min 2


def test_ev_parking():
    """EV parking with assigned/unassigned split."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_parking(site, project)

    ev = next(r for r in results if r.name == "ev_receptacles")
    assert ev.value > 0


def test_parking_results_have_authority():
    """All parking results should have authority metadata."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, _ = calculate_parking(site, project)

    for r in results:
        assert r.code_cycle != "", f"{r.name} missing code_cycle"
