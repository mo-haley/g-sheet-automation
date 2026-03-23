from __future__ import annotations

"""Base rule class that all calculation rules inherit from."""

from abc import ABC, abstractmethod

from config.code_version import CODE_CYCLE, TOOL_VERSION
from models.issue import ReviewIssue
from models.result import CalcResult
from models.site import Site
from models.project import Project


class BaseRule(ABC):
    """Abstract base for all deterministic and advisory rules.

    Every rule must declare its authority_id and produce CalcResult objects
    with full traceability.
    """

    authority_id: str = ""
    code_section: str = ""
    topic: str = ""

    @abstractmethod
    def evaluate(
        self, site: Site, project: Project
    ) -> tuple[list[CalcResult], list[ReviewIssue]]:
        """Run the rule and return results plus any review issues."""

    def _make_result(self, name: str, value, unit: str = "", **kwargs) -> CalcResult:
        """Helper to build a CalcResult with standard metadata."""
        return CalcResult(
            name=name,
            value=value,
            unit=unit,
            code_section=kwargs.get("code_section", self.code_section),
            code_cycle=CODE_CYCLE["label"],
            rule_version=TOOL_VERSION,
            authority_id=kwargs.get("authority_id", self.authority_id),
            determinism=kwargs.get("determinism", "deterministic"),
            confidence=kwargs.get("confidence", "high"),
            formula=kwargs.get("formula", ""),
            inputs_used=kwargs.get("inputs_used", {}),
            intermediate_steps=kwargs.get("intermediate_steps", []),
            review_notes=kwargs.get("review_notes", []),
            assumptions=kwargs.get("assumptions", []),
            data_sources=kwargs.get("data_sources", []),
        )
