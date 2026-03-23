"""Tests for open space calculations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.open_space import calculate_open_space
from models.project import Project, UnitType
from validation.fixtures.projects import simple_r3_project
from validation.fixtures.sites import simple_r3_site


def test_r3_open_space():
    """R3 project with 9 units: 3 studios(100sf) + 4 1BR(125sf) + 2 2BR(125sf) = 300+500+250 = 1050."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_open_space(site, project)

    os_req = next(r for r in results if r.name == "open_space_required")
    # 3*100 + 4*125 + 2*125 = 300 + 500 + 250 = 1050
    assert os_req.value == 1050.0


def test_small_project_no_open_space():
    """Projects with fewer than 6 units do not require open space."""
    site = simple_r3_site()
    project = Project(
        project_name="Small Project",
        total_units=4,
        unit_mix=[UnitType(label="1BR", count=4, habitable_rooms=3, bedrooms=1)],
    )
    results, issues = calculate_open_space(site, project)

    os_req = next(r for r in results if r.name == "open_space_required")
    assert os_req.value == 0


def test_open_space_authority():
    """Open space results should cite LAMC 12.21 G."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, _ = calculate_open_space(site, project)

    os_req = next(r for r in results if r.name == "open_space_required")
    assert os_req.authority_id == "AUTH-OS-RES"
