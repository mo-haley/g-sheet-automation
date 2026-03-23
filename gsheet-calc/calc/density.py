"""Density calculation orchestrator."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.density import DensityRule


def calculate_density(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the base density calculation."""
    rule = DensityRule()
    return rule.evaluate(site, project)
