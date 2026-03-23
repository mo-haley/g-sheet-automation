"""Area chain calculation orchestrator."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.areas import AreaChainRule


def calculate_areas(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the area chain calculation."""
    rule = AreaChainRule()
    return rule.evaluate(site, project)
