"""Scenario comparison and assembly."""

from models.issue import ReviewIssue
from models.result import CalcResult
from models.scenario import ScenarioResult


def build_base_zoning_scenario(
    calculations: list[CalcResult],
    issues: list[ReviewIssue],
) -> ScenarioResult:
    """Wrap deterministic calculations as the base zoning scenario."""
    blocking = [i for i in issues if i.blocking]
    status = "unresolved" if blocking else "likely_eligible"

    return ScenarioResult(
        name="Base Zoning",
        status=status,
        determinism="deterministic",
        summary="Base zoning entitlements under current code without incentive programs.",
        calculations=calculations,
        issues=issues,
        unresolved=[i.title for i in blocking],
    )
