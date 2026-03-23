"""Loading space calculation orchestrator."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.loading import LoadingRule


def calculate_loading(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the loading space calculation."""
    rule = LoadingRule()
    return rule.evaluate(site, project)
