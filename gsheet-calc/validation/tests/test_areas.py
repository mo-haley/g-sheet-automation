"""Tests for area chain calculations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calc.areas import calculate_areas
from validation.fixtures.projects import (
    corner_lot_project,
    r4_alley_project,
    simple_r3_project,
)
from validation.fixtures.sites import (
    area_mismatch_site,
    chapter_unknown_site,
    corner_lot_site,
    r4_alley_site,
    simple_r3_site,
)


def test_simple_r3_gross_area():
    """Simple R3 site should return gross lot area of 7500 sf."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_areas(site, project)

    gross = next(r for r in results if r.name == "gross_lot_area")
    assert gross.value == 7500.0
    assert gross.unit == "sf"


def test_simple_r3_net_equals_gross_no_dedications():
    """With no dedications, net should equal gross."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, issues = calculate_areas(site, project)

    gross = next(r for r in results if r.name == "gross_lot_area")
    net = next(r for r in results if r.name == "net_lot_area")
    assert net.value == gross.value


def test_r4_alley_credit():
    """R4 site with alley should include half-alley credit in effective density area."""
    site = r4_alley_site()
    project = r4_alley_project()
    results, issues = calculate_areas(site, project)

    effective = next(r for r in results if r.name == "effective_density_area")
    net = next(r for r in results if r.name == "net_lot_area")
    # Half-alley credit: 20/2 * 150 = 1500 sf
    expected_credit = (20.0 / 2.0) * 150.0
    assert effective.value == net.value + expected_credit


def test_area_mismatch_generates_issue():
    """Assessor vs survey mismatch should generate a review issue."""
    site = area_mismatch_site()
    project = simple_r3_project()
    results, issues = calculate_areas(site, project)

    mismatch_issues = [i for i in issues if i.id == "CALC-AREA-002"]
    assert len(mismatch_issues) == 1
    assert mismatch_issues[0].severity == "high"


def test_chapter_unknown_generates_issue():
    """Unknown chapter applicability should generate a review issue."""
    site = chapter_unknown_site()
    project = simple_r3_project()
    results, issues = calculate_areas(site, project)

    chapter_issues = [i for i in issues if i.id == "CALC-AREA-003"]
    assert len(chapter_issues) == 1


def test_corner_lot_dedications():
    """Corner lot should show dedications reducing net area."""
    site = corner_lot_site()
    project = corner_lot_project()
    results, issues = calculate_areas(site, project)

    net = next(r for r in results if r.name == "net_lot_area")
    gross = next(r for r in results if r.name == "gross_lot_area")
    # Net should be less than gross due to corner cuts
    assert net.value < gross.value


def test_all_area_results_have_authority():
    """Every area result should have an authority_id."""
    site = simple_r3_site()
    project = simple_r3_project()
    results, _ = calculate_areas(site, project)

    for r in results:
        assert r.authority_id is not None, f"{r.name} missing authority_id"
        assert r.code_cycle != "", f"{r.name} missing code_cycle"
