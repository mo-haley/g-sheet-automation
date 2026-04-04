"""Transit Oriented Communities (TOC) eligibility screen.

STUB: The TOC program was superseded by CHIP effective February 11, 2025.
Transit geography (TOC tier, proximity to major transit) is preserved in
site data for MIIP/AHIP eligibility screening. Actual TOC density and
parking incentives are now computed in the density module TOC lane.

This file is retained as a stub so any callers referencing screen_toc()
continue to receive a valid ScenarioResult rather than an import error.
Do not delete this file.
"""

from models.project import Project
from models.scenario import ScenarioResult
from models.site import Site


def screen_toc(site: Site, project: Project) -> ScenarioResult:
    """Return a fixed advisory result — TOC program superseded by CHIP.

    Transit geography (toc_tier, near_major_transit) is preserved in site
    data for MIIP/AHIP eligibility screening. Current TOC lane calculations
    are in the density module (density_toc_calc.py).
    """
    return ScenarioResult(
        name="TOC Incentive Program",
        status="superseded",
        determinism="advisory",
        summary=(
            "TOC program superseded by CHIP (Feb 11, 2025). "
            "Transit geography preserved for MIIP/AHIP eligibility screening. "
            "See density module TOC lane for current calculations."
        ),
    )
