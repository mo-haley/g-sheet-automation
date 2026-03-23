"""Assumption tracking for user-provided and inferred values."""

from pydantic import BaseModel, Field


class Assumption(BaseModel):
    field: str
    value: str
    source: str  # user_input / inferred / default
    notes: str = ""


class AssumptionLog:
    """Tracks all assumptions made during a calculation run."""

    def __init__(self) -> None:
        self._assumptions: list[Assumption] = []

    def add(self, field: str, value: str, source: str = "user_input", notes: str = "") -> None:
        self._assumptions.append(
            Assumption(field=field, value=value, source=source, notes=notes)
        )

    def get_all(self) -> list[Assumption]:
        return list(self._assumptions)

    def get_by_source(self, source: str) -> list[Assumption]:
        return [a for a in self._assumptions if a.source == source]
