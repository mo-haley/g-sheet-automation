"""Tests for advisory scenario screens."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from models.project import AffordabilityPlan
from rules.advisory.affordable_housing_screen import screen_100pct_affordable
from rules.advisory.density_bonus_screen import screen_density_bonus
from rules.advisory.streamlining_screen import screen_ab2011, screen_sb423
from rules.advisory.toc_screen import screen_toc
from validation.fixtures.projects import c2_residential_project, simple_r3_project
from validation.fixtures.sites import c2_residential_site, simple_r3_site


def test_toc_screen_no_tier():
    """Site without TOC tier should be unresolved."""
    site = simple_r3_site()  # No TOC tier
    project = simple_r3_project()
    result = screen_toc(site, project)

    assert result.status == "unresolved"
    assert result.determinism == "advisory"
    assert "toc_tier" in result.missing_inputs


def test_toc_screen_with_tier():
    """C2 site with TOC tier 3 should screen as eligible (with caveats)."""
    site = c2_residential_site()  # Has toc_tier=3
    project = c2_residential_project()
    result = screen_toc(site, project)

    assert result.determinism == "advisory"
    assert "Tier 3" in result.summary or any("Tier 3" in n for n in result.eligibility_notes)


def test_density_bonus_no_affordability():
    """Density bonus without affordability should be unresolved."""
    site = simple_r3_site()
    project = simple_r3_project()  # No affordability
    result = screen_density_bonus(site, project)

    assert result.status == "unresolved"
    assert "affordability" in result.missing_inputs


def test_density_bonus_with_affordability():
    """Density bonus with affordability should show sliding scale."""
    site = simple_r3_site()
    project = simple_r3_project()
    project.affordability = AffordabilityPlan(vli_pct=11, market_pct=89)
    result = screen_density_bonus(site, project)

    assert result.status == "likely_eligible"
    assert len(result.indicative_yield_notes) > 0


def test_100pct_affordable_not_affordable():
    """Non-100% affordable project should be ineligible."""
    site = simple_r3_site()
    project = simple_r3_project()
    project.affordability = AffordabilityPlan(li_pct=20, market_pct=80)
    result = screen_100pct_affordable(site, project)

    assert result.status == "likely_ineligible"


def test_sb423_always_unresolved():
    """SB 423 should always be unresolved (HCD check required)."""
    site = simple_r3_site()
    project = simple_r3_project()
    result = screen_sb423(site, project)

    assert result.status == "unresolved"
    assert result.determinism == "advisory"


def test_ab2011_no_corridor():
    """AB 2011 without commercial corridor should be ineligible."""
    site = simple_r3_site()
    project = simple_r3_project()
    project.commercial_corridor_frontage = False
    result = screen_ab2011(site, project)

    assert result.status == "likely_ineligible"


def test_advisory_screens_never_deterministic():
    """No advisory screen should claim deterministic confidence."""
    site = c2_residential_site()
    project = c2_residential_project()

    screens = [
        screen_toc(site, project),
        screen_density_bonus(site, project),
        screen_100pct_affordable(site, project),
        screen_sb423(site, project),
        screen_ab2011(site, project),
    ]

    for sc in screens:
        assert sc.determinism == "advisory", f"{sc.name} claimed non-advisory determinism"
