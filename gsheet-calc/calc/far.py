"""FAR calculation orchestrator."""

from models.far_output import FAROutput
from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.far import FARRule


def calculate_far(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run the FAR calculation (legacy interface)."""
    rule = FARRule()
    return rule.evaluate(site, project)


def calculate_far_full(site: Site, project: Project) -> FAROutput:
    """Run the full FAR determination, returning the structured FAROutput."""
    rule = FARRule()
    return rule.evaluate_full(site, project)
