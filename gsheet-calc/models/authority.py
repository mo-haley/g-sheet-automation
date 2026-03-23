from __future__ import annotations

"""RuleAuthority model for tracking the legal basis of each calculation rule."""

from pydantic import BaseModel, Field


class RuleAuthority(BaseModel):
    id: str
    topic: str
    source_type: str  # statute / municipal_code / guideline / memo / faq
    controlling_authority: str
    secondary_authorities: list[str] = Field(default_factory=list)
    jurisdiction_scope: str = "la_city"
    chapter_scope: str = "both"  # chapter_1 / chapter_1a / both / unknown
    effective_date: str | None = None
    superseded_date: str | None = None
    confidence: str = "high"
    human_review_required: bool = False
    notes: str | None = None
