"""Serializable contracts for deterministic diagnosis and bounded recovery."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiagnosticValidationError(ValueError):
    """Raised when diagnostics input or configuration is invalid."""


class FailureCategory(str, Enum):
    TOOL_FAILURE = "tool_failure"
    TIMEOUT = "timeout"
    INVALID_ARGUMENTS = "invalid_arguments"
    TOOL_UNAVAILABLE = "tool_unavailable"
    REPEATED_FAILURE = "repeated_failure"
    REPEATED_ACTION = "repeated_action"
    EXCESSIVE_USAGE = "excessive_usage"
    EXECUTION_LIMIT_REACHED = "execution_limit_reached"
    INCOMPLETE_EXECUTION = "incomplete_execution"
    POSSIBLE_LOOP = "possible_loop"
    UNKNOWN = "unknown"


CATEGORY_ORDER = (
    FailureCategory.EXECUTION_LIMIT_REACHED,
    FailureCategory.INVALID_ARGUMENTS,
    FailureCategory.TIMEOUT,
    FailureCategory.TOOL_UNAVAILABLE,
    FailureCategory.REPEATED_FAILURE,
    FailureCategory.REPEATED_ACTION,
    FailureCategory.POSSIBLE_LOOP,
    FailureCategory.EXCESSIVE_USAGE,
    FailureCategory.INCOMPLETE_EXECUTION,
    FailureCategory.TOOL_FAILURE,
    FailureCategory.UNKNOWN,
)


class PatternType(str, Enum):
    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    REPEATED_ERROR = "repeated_error"
    REPEATED_INVALID_ARGUMENTS = "repeated_invalid_arguments"
    CONSECUTIVE_FAILURES = "consecutive_failures"
    RETRIES_WITHOUT_PROGRESS = "retries_without_progress"
    ALTERNATING_FAILURES = "alternating_failures"
    REPEATED_ACTION = "repeated_action"
    REPEATED_ARGUMENTS = "repeated_arguments"
    POSSIBLE_LOOP = "possible_loop"
    EXCESSIVE_TOOL_CALLS = "excessive_tool_calls"
    TIMEOUT_CLUSTER = "timeout_cluster"
    CONSECUTIVE_TIMEOUTS = "consecutive_timeouts"
    EXECUTION_LIMIT_SIGNAL = "execution_limit_signal"


class PatternSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecoveryAction(str, Enum):
    RETRY_TOOL = "retry_tool"
    RETRY_WITH_MODIFIED_ARGUMENTS = "retry_with_modified_arguments"
    SWITCH_TOOL = "switch_tool"
    RESUME_EXECUTION = "resume_execution"
    CONTINUE_WITH_PARTIAL_RESULTS = "continue_with_partial_results"
    REQUEST_HUMAN_REVIEW = "request_human_review"
    ABORT_EXECUTION = "abort_execution"


class RecoveryRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecoveryStatus(str, Enum):
    RECOVERED = "recovered"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    NO_ACTION = "no_action"


class RecoveryAttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FinalExecutionOutcome(str, Enum):
    COMPLETED = "completed"
    RECOVERED = "recovered"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    source: str
    event_ids: tuple[str, ...] = ()
    observed_value: Optional[float] = None
    threshold: Optional[float] = None


class FailurePattern(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_type: PatternType
    severity: PatternSeverity
    description: str
    event_ids: tuple[str, ...] = ()
    tool_name: Optional[str] = None
    count: int = Field(ge=0)
    threshold: Optional[int] = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MLRiskSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    probability: float
    threshold: float = 0.5
    model_name: Optional[str] = None
    source: str = "provided"

    @field_validator("probability", "threshold")
    @classmethod
    def values_must_be_probabilities(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("ML risk probability and threshold must be between 0 and 1")
        return float(value)

    @property
    def predicted_failure(self) -> bool:
        return self.probability >= self.threshold

    def to_evidence(self) -> Evidence:
        return Evidence(
            description=(
                f"ML model {self.model_name or 'unspecified'} reported failure probability "
                f"{self.probability:.4f} against threshold {self.threshold:.4f}; "
                "this is predictive risk, not confirmed failure."
            ),
            source=f"ml:{self.source}",
            observed_value=self.probability,
            threshold=self.threshold,
        )


class FailureDiagnosis(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_failure: bool
    primary_category: FailureCategory
    secondary_categories: tuple[FailureCategory, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    execution_id: Optional[str] = None
    task_id: Optional[str] = None
    tool_name: Optional[str] = None
    terminal_observed: bool = False
    execution_status: Optional[str] = None
    partial_results_available: bool = False
    patterns: tuple[FailurePattern, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    ml_risk: Optional[MLRiskSignal] = None


class RecoveryRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: RecoveryAction
    diagnosis_category: FailureCategory
    target_tool: Optional[str] = None
    risk: RecoveryRisk
    requires_human_approval: bool
    max_attempts: int = Field(ge=1, le=5)
    rationale: str
    expected_benefit: str
    evidence_event_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def action_target_is_consistent(self) -> "RecoveryRecommendation":
        if self.action in {
            RecoveryAction.RETRY_TOOL,
            RecoveryAction.RETRY_WITH_MODIFIED_ARGUMENTS,
            RecoveryAction.SWITCH_TOOL,
        } and not self.target_tool:
            raise ValueError(f"{self.action.value} requires a target_tool")
        if self.action in {
            RecoveryAction.REQUEST_HUMAN_REVIEW,
            RecoveryAction.ABORT_EXECUTION,
            RecoveryAction.RETRY_WITH_MODIFIED_ARGUMENTS,
            RecoveryAction.RESUME_EXECUTION,
        }:
            if self.requires_human_approval is not True:
                raise ValueError(f"{self.action.value} must require human approval")
        return self


class RecoveryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_total_attempts: int = Field(default=2, ge=1, le=5)
    max_attempts_per_recommendation: int = Field(default=1, ge=1, le=3)
    retry_backoff_seconds: tuple[float, ...] = (0.1, 0.25, 0.5)
    allow_medium_risk: bool = True
    allow_modified_arguments: bool = True
    approved_tool_names: tuple[str, ...] = ("calculator", "search", "company_data")
    retry_safe_tool_names: tuple[str, ...] = ("calculator", "search", "company_data")

    @model_validator(mode="after")
    def tool_policy_is_consistent(self) -> "RecoveryPolicy":
        if not self.retry_backoff_seconds or any(
            not 0.0 < delay <= 5.0 for delay in self.retry_backoff_seconds
        ):
            raise ValueError("Retry backoff delays must be positive and at most 5 seconds")
        if any(not name.strip() for name in self.approved_tool_names + self.retry_safe_tool_names):
            raise ValueError("Recovery policy tool names must be non-empty")
        if not set(self.retry_safe_tool_names).issubset(self.approved_tool_names):
            raise ValueError("Every retry-safe tool must also be approved")
        return self

    @property
    def absolute_attempt_limit(self) -> int:
        return self.max_total_attempts


class RecoveryOutcome(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    success: bool
    recovery_execution_id: Optional[str] = None
    output: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Executor-measured action duration in milliseconds, excluding controller backoff.",
    )
    actual_action: Optional[RecoveryAction] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("duration_ms")
    @classmethod
    def duration_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Recovery outcome duration must be finite")
        return value


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    attempt_id: str
    recovery_execution_id: str
    original_execution_id: Optional[str]
    triggering_event_id: Optional[str] = None
    attempt_number: int = Field(ge=1)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=5.0)
    chain_depth: Literal[1] = 1
    tool_call_budget: Literal[1] = 1
    diagnosis: FailureDiagnosis
    recommendation: RecoveryRecommendation


class RecoveryAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt_id: str
    session_id: str
    original_execution_id: Optional[str]
    triggering_event_id: Optional[str] = None
    diagnosis_category: FailureCategory
    recovery_execution_id: str
    attempt_number: int = Field(ge=1)
    chain_depth: Literal[1] = 1
    action: RecoveryAction
    actual_action: RecoveryAction
    target_tool: Optional[str] = None
    risk: RecoveryRisk
    status: RecoveryAttemptStatus
    started_at: datetime
    completed_at: datetime
    output: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Executor-measured action duration when supplied; otherwise elapsed attempt time. "
            "Attempt timestamps include controller backoff."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("duration_ms")
    @classmethod
    def duration_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Recovery attempt duration must be finite")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Recovery timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def completion_is_not_before_start(self) -> "RecoveryAttempt":
        if self.completed_at < self.started_at:
            raise ValueError("Recovery attempt completion must not precede its start")
        if self.duration_ms is not None:
            elapsed_ms = (self.completed_at - self.started_at).total_seconds() * 1000
            if self.duration_ms > elapsed_ms + 0.001:
                raise ValueError(
                    "Recovery attempt duration must not exceed its timestamp envelope"
                )
        return self


class RecoverySession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    original_execution_id: Optional[str]
    status: RecoveryStatus
    final_execution_outcome: FinalExecutionOutcome
    diagnosis: FailureDiagnosis
    recommendations: tuple[RecoveryRecommendation, ...]
    attempts: tuple[RecoveryAttempt, ...]
    blocked_reasons: tuple[str, ...] = ()
    successful_recovery_execution_id: Optional[str] = None
    started_at: datetime
    completed_at: datetime
    policy: RecoveryPolicy
    telemetry_version: str = "1.0.0"

    @field_validator("started_at", "completed_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Recovery timestamps must be timezone-aware")
        return value

    @property
    def executed_attempt_count(self) -> int:
        return len(self.attempts)


class DiagnosisReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    diagnosis: FailureDiagnosis
    recommendations: tuple[RecoveryRecommendation, ...]
    ml_error: Optional[str] = None
