from __future__ import annotations

"""CalcResult model for traceable calculation outputs."""

from typing import Any

from pydantic import BaseModel, Field


class CalcResult(BaseModel):
    name: str
    value: Any
    unit: str = ""

    formula: str = ""
    inputs_used: dict = Field(default_factory=dict)
    intermediate_steps: list[str] = Field(default_factory=list)

    code_section: str | None = None
    code_cycle: str = ""
    rule_version: str = ""
    authority_id: str | None = None

    determinism: str = "deterministic"  # deterministic / advisory / manual_only
    confidence: str = "high"  # high / medium / low
    review_notes: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
