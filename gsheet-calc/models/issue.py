"""ReviewIssue model for tracking items requiring human review."""

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    id: str
    category: str
    severity: str = "medium"  # critical / high / medium / low
    status: str = "open"  # open / resolved / assumed
    title: str
    description: str
    affected_fields: list[str] = Field(default_factory=list)
    suggested_review_role: str = ""  # architect / planner / zoning consultant
    blocking: bool = False
