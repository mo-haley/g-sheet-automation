"""ScenarioResult model for advisory pathway screening outputs."""

from pydantic import BaseModel, Field

from models.issue import ReviewIssue
from models.result import CalcResult


class ScenarioResult(BaseModel):
    name: str
    status: str  # likely_eligible / likely_ineligible / unresolved
    determinism: str = "advisory"
    summary: str = ""

    eligibility_notes: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions_used: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

    indicative_yield_notes: list[str] = Field(default_factory=list)
    indicative_parking_notes: list[str] = Field(default_factory=list)
    labor_notes: list[str] = Field(default_factory=list)
    process_notes: list[str] = Field(default_factory=list)

    calculations: list[CalcResult] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
