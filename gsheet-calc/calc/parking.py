"""Parking calculation orchestrator: auto + accessible + bike + EV."""

from models.issue import ReviewIssue
from models.project import Project
from models.result import CalcResult
from models.site import Site
from rules.deterministic.parking_accessible import AccessibleParkingRule
from rules.deterministic.parking_auto import AutoParkingRule
from rules.deterministic.parking_bike import BikeParkingRule
from rules.deterministic.parking_ev import EVParkingRule


def calculate_parking(site: Site, project: Project) -> tuple[list[CalcResult], list[ReviewIssue]]:
    """Run all parking sub-calculations."""
    all_results: list[CalcResult] = []
    all_issues: list[ReviewIssue] = []

    for rule_cls in [AutoParkingRule, AccessibleParkingRule, BikeParkingRule, EVParkingRule]:
        rule = rule_cls()
        calc_results, calc_issues = rule.evaluate(site, project)
        all_results.extend(calc_results)
        all_issues.extend(calc_issues)

    return all_results, all_issues
