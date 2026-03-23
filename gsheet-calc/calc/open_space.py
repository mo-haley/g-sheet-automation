"""Open space calculation orchestrator."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.open_space import OpenSpaceRule


def calculate_open_space(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the open space calculation."""
    rule = OpenSpaceRule()
    return rule.evaluate(site, project)
