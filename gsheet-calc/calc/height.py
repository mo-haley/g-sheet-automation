"""Height calculation orchestrator."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.height import HeightRule


def calculate_height(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the height/story limit calculation."""
    rule = HeightRule()
    return rule.evaluate(site, project)
