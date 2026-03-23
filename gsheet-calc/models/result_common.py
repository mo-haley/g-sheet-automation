from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


class RunStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    ERROR = "error"


class CoverageLevel(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    THIN = "thin"
    UNCERTAIN = "uncertain"
    NONE = "none"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNRESOLVED = "unresolved"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ActionPosture(str, Enum):
    CAN_RELY_WITH_REVIEW = "can_rely_with_review"
    ACT_ON_DETECTED_ITEMS_BUT_REVIEW_FOR_GAPS = "act_on_detected_items_but_review_for_gaps"
    MANUAL_INPUT_REQUIRED = "manual_input_required"
    AUTHORITY_CONFIRMATION_REQUIRED = "authority_confirmation_required"
    INSUFFICIENT_FOR_PERMIT_USE = "insufficient_for_permit_use"


class OverallReadiness(str, Enum):
    READY_FOR_DRAFT = "ready_for_draft"
    DRAFT_ONLY_NOT_FOR_ISSUE = "draft_only_not_for_issue"
    NOT_READY_FOR_PERMIT = "not_ready_for_permit"
    INSUFFICIENT_INPUT = "insufficient_input"


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_type: str
    label: str
    locator: Optional[str] = None
    notes: Optional[str] = None


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_types: List[str] = Field(default_factory=list)
    authoritative_sources_used: List[str] = Field(default_factory=list)
    non_authoritative_sources_used: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    plain_language_result: str
    action_posture: ActionPosture


class BaseMessage(BaseModel):
    """
    Shared structure for findings, issues, warnings, and assumptions.

    Keep this intentionally explicit.
    No hidden inference fields.
    """
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    citations: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class Finding(BaseMessage):
    supports_decision: bool = True
    severity: Severity = Severity.INFO


class Issue(BaseMessage):
    severity: Severity
    blocking: bool = False
    needs_user_input: bool = False
    needs_architect_judgment: bool = False
    needs_authority_confirmation: bool = False


class WarningMessage(BaseMessage):
    pass


class Assumption(BaseMessage):
    user_supplied: bool = False


class ModuleResult(BaseModel):
    """
    Canonical module-level contract for all zoning / code / sheet-prep modules.

    Rules:
    - module_payload is module-specific
    - everything else should remain stable across modules
    - use assumptions explicitly; never silently infer
    """
    model_config = ConfigDict(extra="forbid")

    module: str
    module_version: str = "v1"

    run_status: RunStatus
    coverage_level: CoverageLevel
    confidence: ConfidenceLevel

    blocking: bool = False

    inputs_summary: Dict[str, Any] = Field(default_factory=dict)

    interpretation: Interpretation

    findings: List[Finding] = Field(default_factory=list)
    issues: List[Issue] = Field(default_factory=list)
    warnings: List[WarningMessage] = Field(default_factory=list)
    assumptions: List[Assumption] = Field(default_factory=list)

    citations: List[Citation] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    module_payload: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_blocking_consistency(self) -> "ModuleResult":
        """
        Keep top-level blocking aligned with issue content.
        Top-level blocking may be True if any issue is blocking.
        """
        has_blocking_issue = any(issue.blocking for issue in self.issues)
        if has_blocking_issue and not self.blocking:
            self.blocking = True
        return self

    @model_validator(mode="after")
    def validate_status_confidence_alignment(self) -> "ModuleResult":
        """
        Guardrails for trust signaling.
        These are intentionally conservative.
        """
        if self.run_status == RunStatus.ERROR and self.confidence != ConfidenceLevel.UNRESOLVED:
            raise ValueError("run_status='error' requires confidence='unresolved'")

        if self.run_status == RunStatus.BLOCKED and not self.blocking:
            raise ValueError("run_status='blocked' requires blocking=True")

        if self.coverage_level == CoverageLevel.NONE and self.run_status == RunStatus.OK:
            raise ValueError("coverage_level='none' is incompatible with run_status='ok'")

        return self

    def has_errors_or_blockers(self) -> bool:
        return self.run_status in {RunStatus.ERROR, RunStatus.BLOCKED} or self.blocking

    def has_uncertainty(self) -> bool:
        return (
            self.coverage_level in {CoverageLevel.PARTIAL, CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE}
            or self.confidence in {ConfidenceLevel.LOW, ConfidenceLevel.UNRESOLVED}
            or len(self.warnings) > 0
        )

    def requires_manual_input(self) -> bool:
        """True if any formal Issue flags needs_user_input, or action_posture signals it."""
        return (
            any(issue.needs_user_input for issue in self.issues)
            or self.interpretation.action_posture == ActionPosture.MANUAL_INPUT_REQUIRED
        )

    def requires_authority_confirmation(self) -> bool:
        """True if any formal Issue flags needs_authority_confirmation, or action_posture signals it."""
        return (
            any(issue.needs_authority_confirmation for issue in self.issues)
            or self.interpretation.action_posture == ActionPosture.AUTHORITY_CONFIRMATION_REQUIRED
        )

    def requires_architect_judgment(self) -> bool:
        return any(issue.needs_architect_judgment for issue in self.issues)


class AppSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: List[str] = Field(default_factory=list)
    unresolved: List[str] = Field(default_factory=list)
    manual_inputs_required: List[str] = Field(default_factory=list)
    authority_confirmations_required: List[str] = Field(default_factory=list)


class DecisionPosture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_generate_draft_g_sheet: bool
    requires_manual_review_before_issue: bool
    blocking_modules: List[str] = Field(default_factory=list)
    non_blocking_uncertainties: List[str] = Field(default_factory=list)


class AppResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str = "v1"
    project_id: str

    overall_status: RunStatus
    overall_readiness: OverallReadiness

    module_results: List[ModuleResult] = Field(default_factory=list)

    summary: AppSummary
    decision_posture: DecisionPosture

    @classmethod
    def from_module_results(
        cls,
        project_id: str,
        module_results: List[ModuleResult],
    ) -> "AppResult":
        """
        Conservative aggregator:
        - any error -> overall error
        - any blocking module -> overall blocked
        - otherwise any partial/uncertain module -> overall partial
        - else ok

        requires_manual_input and requires_authority_confirmation are derived
        from both formal Issue flags and action_posture, since current adapters
        signal these states via action_posture rather than Issue objects.
        """
        blocking_modules = [m.module for m in module_results if m.blocking or m.run_status == RunStatus.BLOCKED]
        error_modules = [m.module for m in module_results if m.run_status == RunStatus.ERROR]

        uncertain_modules = [
            m.module
            for m in module_results
            if (
                m.run_status == RunStatus.PARTIAL
                or m.coverage_level in {CoverageLevel.PARTIAL, CoverageLevel.THIN, CoverageLevel.UNCERTAIN, CoverageLevel.NONE}
                or m.confidence in {ConfidenceLevel.LOW, ConfidenceLevel.UNRESOLVED}
            )
            and m.module not in blocking_modules
        ]

        if error_modules:
            overall_status = RunStatus.ERROR
        elif blocking_modules:
            overall_status = RunStatus.BLOCKED
        elif uncertain_modules:
            overall_status = RunStatus.PARTIAL
        else:
            overall_status = RunStatus.OK

        requires_manual_input = []
        authority_confirmations_required = []
        unresolved = []
        confirmed = []

        for module in module_results:
            if module.requires_manual_input():
                requires_manual_input.append(module.module)

            if module.requires_authority_confirmation():
                authority_confirmations_required.append(module.module)

            if module.has_errors_or_blockers() or module.has_uncertainty():
                unresolved.append(module.module)
            else:
                confirmed.append(module.module)

        overall_readiness = cls._derive_readiness(
            overall_status=overall_status,
            module_results=module_results,
        )

        return cls(
            project_id=project_id,
            overall_status=overall_status,
            overall_readiness=overall_readiness,
            module_results=module_results,
            summary=AppSummary(
                confirmed=confirmed,
                unresolved=unresolved,
                manual_inputs_required=requires_manual_input,
                authority_confirmations_required=authority_confirmations_required,
            ),
            decision_posture=DecisionPosture(
                can_generate_draft_g_sheet=overall_status in {RunStatus.OK, RunStatus.PARTIAL},
                requires_manual_review_before_issue=overall_status != RunStatus.OK,
                blocking_modules=blocking_modules,
                non_blocking_uncertainties=uncertain_modules,
            ),
        )

    @staticmethod
    def _derive_readiness(
        overall_status: RunStatus,
        module_results: List[ModuleResult],
    ) -> OverallReadiness:
        """
        First-pass readiness logic.
        Adjust later once you classify core vs contextual modules more formally.

        INSUFFICIENT_INPUT is checked before blocking/error so that a module
        needing user-supplied data (e.g. architect floor area) is surfaced
        explicitly rather than silently folded into NOT_READY_FOR_PERMIT.
        """
        if any(m.requires_manual_input() for m in module_results):
            return OverallReadiness.INSUFFICIENT_INPUT

        if overall_status == RunStatus.ERROR:
            return OverallReadiness.NOT_READY_FOR_PERMIT

        if overall_status == RunStatus.BLOCKED:
            return OverallReadiness.NOT_READY_FOR_PERMIT

        if overall_status == RunStatus.PARTIAL:
            return OverallReadiness.DRAFT_ONLY_NOT_FOR_ISSUE

        return OverallReadiness.READY_FOR_DRAFT
