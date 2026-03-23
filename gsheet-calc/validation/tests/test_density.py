"""Tests for density calculations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.density import calculate_density
from validation.fixtures.projects import c2_residential_project, simple_r3_project
from validation.fixtures.sites import c2_residential_site, simple_r3_site


def test_r3_density():
    """R3 zone with 7500sf lot: floor(7500/800) = 9 units."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_density(site, project)

    density = next(r for r in results if r.name == "base_density")
    assert density.value == 9  # floor(7500 / 800)
    assert density.unit == "dwelling units"


def test_c2_density():
    """C2 zone with 10000sf lot: floor(10000/400) = 25 units."""
    site = c2_residential_site()
    project = c2_residential_project()
    results, issues = calculate_density(site, project)

    density = next(r for r in results if r.name == "base_density")
    assert density.value == 25  # floor(10000 / 400)


def test_density_has_no_bonus():
    """Density result should note that bonus is NOT included."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, _ = calculate_density(site, project)

    density = next(r for r in results if r.name == "base_density")
    assert any("bonus" in note.lower() or "NOT included" in note for note in density.review_notes)


def test_density_code_section():
    """Density result should cite LAMC code section."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, _ = calculate_density(site, project)

    density = next(r for r in results if r.name == "base_density")
    assert density.authority_id == "AUTH-DENSITY"
