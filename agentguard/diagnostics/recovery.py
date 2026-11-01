"""Bounded recovery execution with hard limits and explicit approval gates."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import sleep
from typing import Callable, Protocol, Sequence
from uuid import uuid4

from agentguard.diagnostics.types import (
    DiagnosticValidationError,
    FailureCategory,
    FailureDiagnosis,
    FinalExecutionOutcome,
    RecoveryAction,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryOutcome,
    RecoveryPolicy,
    RecoveryRecommendation,
    RecoveryRequest,
    RecoveryRisk,
    RecoverySession,
    RecoveryStatus,
)
from agentguard.telemetry.privacy import redact_metadata, redact_text


class RecoveryExecutionError(RuntimeError):
    """Raised when recovery execution is invoked in an invalid or nested way."""


class RecoveryExecutor(Protocol):
    def execute(self, request: RecoveryRequest) -> RecoveryOutcome:
        """Perform exactly one bounded recovery action without recursive recovery."""


class RecoveryTelemetrySink(Protocol):
    def record(self, session: RecoverySession) -> None:
        """Record the completed recovery session."""


class RecoveryAttemptLedger(Protocol):
    def claim(self, execution_id: str, maximum: int) -> bool:
        """Atomically consume one attempt from an execution's recovery budget."""


class InMemoryRecoveryAttemptLedger:
    """Thread-safe budget shared only by controllers given this ledger instance.

    The controller's default ledger is local to that controller. Inject one shared
    instance to coordinate controllers in a process; use the database-backed ledger
    for durable cross-process and restart-safe enforcement.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def claim(self, execution_id: str, maximum: int) -> bool:
        with self._lock:
            used = self._counts.get(execution_id, 0)
            if used >= maximum:
                return False
            self._counts[execution_id] = used + 1
            return True


class InMemoryRecoveryTelemetry:
    def __init__(self) -> None:
        self.sessions: list[RecoverySession] = []

    def record(self, session: RecoverySession) -> None:
        self.sessions.append(session)

    def clear(self) -> None:
        self.sessions.clear()


SAFE_RETRY_CATEGORIES = {
    FailureCategory.TIMEOUT,
    FailureCategory.TOOL_UNAVAILABLE,
}
NON_AUTOMATIC_ACTIONS = {
    RecoveryAction.REQUEST_HUMAN_REVIEW,
    RecoveryAction.ABORT_EXECUTION,
    RecoveryAction.RETRY_WITH_MODIFIED_ARGUMENTS,
    RecoveryAction.RESUME_EXECUTION,
}


class BoundedRecoveryController:
    def __init__(
        self,
        policy: RecoveryPolicy | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        telemetry: RecoveryTelemetrySink | None = None,
        attempt_ledger: RecoveryAttemptLedger | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.policy = policy or RecoveryPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (lambda: str(uuid4()))
        self.telemetry = telemetry if telemetry is not None else InMemoryRecoveryTelemetry()
        self.attempt_ledger = (
            attempt_ledger if attempt_ledger is not None else InMemoryRecoveryAttemptLedger()
        )
        self.sleeper = sleeper or sleep
        self._active = False
        self._active_lock = Lock()

    def recover(
        self,
        diagnosis: FailureDiagnosis,
        recommendations: Sequence[RecoveryRecommendation],
        executor: RecoveryExecutor,
        *,
        original_execution_id: str | None = None,
    ) -> RecoverySession:
        if self._active:
            raise RecoveryExecutionError("Nested or recursive recovery execution is not permitted")
        if (original_execution_id and diagnosis.execution_id
                and original_execution_id != diagnosis.execution_id):
            raise DiagnosticValidationError(
                "original_execution_id must match the diagnosed execution"
            )
        if original_execution_id is not None and not original_execution_id.strip():
            raise DiagnosticValidationError("original_execution_id must not be empty")
        triggering_event_ids = tuple(
            _validated_trigger_event_id(diagnosis, recommendation)
            for recommendation in recommendations
        )
        if not diagnosis.is_failure and not (
            diagnosis.ml_risk is not None and diagnosis.ml_risk.predicted_failure
        ):
            session = self._session(
                diagnosis=diagnosis,
                recommendations=tuple(recommendations),
                attempts=(),
                blocked_reasons=(),
                original_execution_id=original_execution_id or diagnosis.execution_id,
                status=RecoveryStatus.NO_ACTION,
            )
            self._record(session)
            return session
        if not recommendations:
            session = self._session(
                diagnosis=diagnosis,
                recommendations=(),
                attempts=(),
                blocked_reasons=("No recovery recommendation was available",),
                original_execution_id=original_execution_id or diagnosis.execution_id,
                status=RecoveryStatus.BLOCKED,
            )
            self._record(session)
            return session

        original = original_execution_id or diagnosis.execution_id
        if original is None:
            session = self._session(
                diagnosis=diagnosis,
                recommendations=tuple(recommendations),
                attempts=(),
                blocked_reasons=("Recovery requires a stable original execution ID",),
                original_execution_id=None,
                status=RecoveryStatus.BLOCKED,
            )
            self._record(session)
            return session
        session_id = self.id_factory()
        started_at = self._now()
        attempts: list[RecoveryAttempt] = []
        blocked: list[str] = []
        successful_recovery_id: str | None = None
        permanent_stop = False
        budget_exhausted = False

        if not self._active_lock.acquire(blocking=False):
            raise RecoveryExecutionError("This controller is already executing a recovery session")
        self._active = True
        try:
            for recommendation_index, recommendation in enumerate(recommendations):
                if successful_recovery_id is not None:
                    break
                if len(attempts) >= self.policy.absolute_attempt_limit:
                    blocked.append("Global recovery-attempt limit reached")
                    break
                unsafe_reason = self._unsafe_reason(diagnosis, recommendation)
                if unsafe_reason is not None:
                    blocked.append(unsafe_reason)
                    continue
                if recommendation.action in NON_AUTOMATIC_ACTIONS:
                    blocked.append(
                        f"{recommendation.action.value} requires explicit human action and was not executed automatically"
                    )
                    continue
                if recommendation.requires_human_approval:
                    blocked.append(
                        f"{recommendation.action.value} requires human approval; this service does not execute approval-gated actions"
                    )
                    continue
                if recommendation.risk == RecoveryRisk.HIGH:
                    blocked.append(f"{recommendation.action.value} is high risk and cannot run automatically")
                    continue
                if recommendation.risk == RecoveryRisk.MEDIUM and not self.policy.allow_medium_risk:
                    blocked.append(f"{recommendation.action.value} is medium risk and policy forbids it")
                    continue

                trigger_event_id = triggering_event_ids[recommendation_index]
                if trigger_event_id is None:
                    blocked.append(
                        "Automatic recovery requires a triggering event present in diagnosis evidence"
                    )
                    continue

                allowed_attempts = min(
                    recommendation.max_attempts,
                    self.policy.max_attempts_per_recommendation,
                    self.policy.absolute_attempt_limit - len(attempts),
                )
                for attempt_number in range(1, max(0, allowed_attempts) + 1):
                    if not self.attempt_ledger.claim(original, self.policy.absolute_attempt_limit):
                        blocked.append("Per-execution recovery-attempt limit reached")
                        budget_exhausted = True
                        break
                    attempt, outcome = self._execute_once(
                        session_id=session_id,
                        original_execution_id=original,
                        diagnosis=diagnosis,
                        recommendation=recommendation,
                        trigger_event_id=trigger_event_id,
                        recommendation_index=recommendation_index,
                        attempt_number=len(attempts) + 1,
                        executor=executor,
                    )
                    attempts.append(attempt)
                    if outcome.success:
                        successful_recovery_id = attempt.recovery_execution_id
                        break
                    if recommendation.action != RecoveryAction.RETRY_TOOL:
                        break
                    if not _is_retryable_recovery_error(outcome.error_type):
                        blocked.append(
                            "Recovery attempt returned a permanent or unclassified error; no further automatic retry was made"
                        )
                        permanent_stop = True
                        break
                if len(attempts) >= self.policy.absolute_attempt_limit:
                    blocked.append("Global recovery-attempt limit reached")
                    break
                if permanent_stop:
                    break
                if budget_exhausted:
                    break
        finally:
            self._active = False
            self._active_lock.release()

        if successful_recovery_id is not None:
            status = RecoveryStatus.RECOVERED
        elif attempts:
            status = RecoveryStatus.EXHAUSTED
        elif blocked:
            status = RecoveryStatus.BLOCKED
        else:
            status = RecoveryStatus.NO_ACTION
        session = self._session(
            diagnosis=diagnosis,
            recommendations=tuple(recommendations),
            attempts=tuple(attempts),
            blocked_reasons=_unique(blocked),
            original_execution_id=original,
            status=status,
            session_id=session_id,
            successful_recovery_execution_id=successful_recovery_id,
            started_at=started_at,
        )
        self._record(session)
        return session

    def _execute_once(
        self,
        *,
        session_id: str,
        original_execution_id: str | None,
        diagnosis: FailureDiagnosis,
        recommendation: RecoveryRecommendation,
        trigger_event_id: str,
        recommendation_index: int,
        attempt_number: int,
        executor: RecoveryExecutor,
    ) -> tuple[RecoveryAttempt, RecoveryOutcome]:
        attempt_id = self.id_factory()
        recovery_execution_id = self.id_factory()
        backoff_seconds = 0.0
        if recommendation.action == RecoveryAction.RETRY_TOOL:
            delays = self.policy.retry_backoff_seconds
            backoff_seconds = delays[min(attempt_number - 1, len(delays) - 1)]
        request = RecoveryRequest(
            session_id=session_id,
            attempt_id=attempt_id,
            recovery_execution_id=recovery_execution_id,
            original_execution_id=original_execution_id,
            triggering_event_id=trigger_event_id,
            attempt_number=attempt_number,
            backoff_seconds=backoff_seconds,
            tool_call_budget=1,
            diagnosis=diagnosis,
            recommendation=recommendation,
        )
        started_at = self._now()
        try:
            if backoff_seconds:
                self.sleeper(backoff_seconds)
            outcome = executor.execute(request)
            if not isinstance(outcome, RecoveryOutcome):
                outcome = RecoveryOutcome.model_validate(outcome)
        except Exception as exc:
            outcome = RecoveryOutcome(
                success=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        completed_at = self._now()
        if completed_at < started_at:
            raise DiagnosticValidationError(
                "Recovery attempt completion timestamp precedes its start timestamp"
            )
        recovery_execution_id = outcome.recovery_execution_id or recovery_execution_id
        duration = outcome.duration_ms
        elapsed_ms = max(0.0, (completed_at - started_at).total_seconds() * 1000)
        if duration is None:
            duration = elapsed_ms
        elif duration > elapsed_ms + 0.001:
            raise DiagnosticValidationError(
                "Executor-reported recovery duration exceeds the attempt timestamp envelope"
            )
        metadata = redact_metadata(dict(outcome.metadata))
        metadata["recommendation_index"] = recommendation_index
        metadata["backoff_seconds"] = backoff_seconds
        attempt = RecoveryAttempt(
            attempt_id=attempt_id,
            session_id=session_id,
            original_execution_id=original_execution_id,
            triggering_event_id=trigger_event_id,
            diagnosis_category=diagnosis.primary_category,
            recovery_execution_id=recovery_execution_id,
            attempt_number=attempt_number,
            action=recommendation.action,
            actual_action=outcome.actual_action or recommendation.action,
            target_tool=recommendation.target_tool,
            risk=recommendation.risk,
            status=RecoveryAttemptStatus.SUCCEEDED if outcome.success else RecoveryAttemptStatus.FAILED,
            started_at=started_at,
            completed_at=completed_at,
            output=redact_text(outcome.output),
            error_type=outcome.error_type,
            error_message=redact_text(outcome.error_message),
            duration_ms=duration,
            metadata=metadata,
        )
        return attempt, outcome

    def _unsafe_reason(
        self,
        diagnosis: FailureDiagnosis,
        recommendation: RecoveryRecommendation,
    ) -> str | None:
        category = diagnosis.primary_category
        if recommendation.diagnosis_category != category:
            return "Recommendation diagnosis category does not match the current diagnosis"
        if recommendation.action == RecoveryAction.RETRY_TOOL:
            if category not in SAFE_RETRY_CATEGORIES:
                return f"Raw retry is unsafe for category {category.value}"
            if not recommendation.target_tool:
                return "Raw retry requires a known target tool"
            if recommendation.target_tool != diagnosis.tool_name:
                return "Retry target must match the tool supported by the diagnosis"
            if recommendation.target_tool not in self.policy.retry_safe_tool_names:
                return "Retry target is not configured as an approved retry-safe tool"
        elif recommendation.action == RecoveryAction.RETRY_WITH_MODIFIED_ARGUMENTS:
            if not self.policy.allow_modified_arguments:
                return "Policy forbids modified-argument retries"
            if category != FailureCategory.INVALID_ARGUMENTS:
                return "Modified-argument recovery is only valid for invalid arguments"
            if not recommendation.target_tool:
                return "Modified-argument recovery requires a known target tool"
            if recommendation.target_tool != diagnosis.tool_name:
                return "Modified-argument target must match the diagnosed tool"
            if recommendation.target_tool not in self.policy.approved_tool_names:
                return "Modified-argument recovery targets a tool outside the approved tool set"
        elif recommendation.action == RecoveryAction.SWITCH_TOOL:
            if category not in {
                FailureCategory.TOOL_UNAVAILABLE,
                FailureCategory.REPEATED_FAILURE,
                FailureCategory.REPEATED_ACTION,
            }:
                return f"Tool switching is not supported for category {category.value}"
            if not diagnosis.tool_name:
                return "Tool switching requires a diagnosed source tool"
            if not recommendation.target_tool:
                return "Tool switching requires a configured alternative"
            if recommendation.target_tool == diagnosis.tool_name:
                return "Tool switching must target a different tool"
            if recommendation.target_tool not in self.policy.approved_tool_names:
                return "Tool switching targets a tool outside the approved tool set"
        elif recommendation.action == RecoveryAction.RESUME_EXECUTION:
            if category != FailureCategory.INCOMPLETE_EXECUTION:
                return "Resuming is only valid for incomplete executions"
        elif recommendation.action == RecoveryAction.CONTINUE_WITH_PARTIAL_RESULTS:
            if category != FailureCategory.INCOMPLETE_EXECUTION or not diagnosis.partial_results_available:
                return "Partial-result continuation requires an incomplete execution with successful results"
        return None

    def _session(
        self,
        *,
        diagnosis: FailureDiagnosis,
        recommendations: tuple[RecoveryRecommendation, ...],
        attempts: tuple[RecoveryAttempt, ...],
        blocked_reasons: tuple[str, ...],
        original_execution_id: str | None,
        status: RecoveryStatus,
        session_id: str | None = None,
        successful_recovery_execution_id: str | None = None,
        started_at: datetime | None = None,
    ) -> RecoverySession:
        start = started_at or self._now()
        end = self._now()
        if end < start:
            end = start
        return RecoverySession(
            session_id=session_id or self.id_factory(),
            original_execution_id=original_execution_id,
            status=status,
            final_execution_outcome=_final_outcome(
                diagnosis, attempts,
                successful_recovery_execution_id,
            ),
            diagnosis=diagnosis,
            recommendations=recommendations,
            attempts=attempts,
            blocked_reasons=blocked_reasons,
            successful_recovery_execution_id=successful_recovery_execution_id,
            started_at=start,
            completed_at=end,
            policy=self.policy,
        )

    def _record(self, session: RecoverySession) -> None:
        self.telemetry.record(session)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise DiagnosticValidationError("Recovery clock must return timezone-aware timestamps")
        return value


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _validated_trigger_event_id(
    diagnosis: FailureDiagnosis,
    recommendation: RecoveryRecommendation,
) -> str | None:
    diagnosis_event_ids = {
        event_id
        for evidence in diagnosis.evidence
        for event_id in evidence.event_ids
        if isinstance(event_id, str) and event_id.strip()
    }
    if recommendation.evidence_event_ids:
        invalid = set(recommendation.evidence_event_ids) - diagnosis_event_ids
        if invalid:
            raise DiagnosticValidationError(
                "Recommendation evidence event IDs must belong to diagnosis evidence"
            )
        return recommendation.evidence_event_ids[0]
    for evidence in diagnosis.evidence:
        for event_id in evidence.event_ids:
            if isinstance(event_id, str) and event_id.strip():
                return event_id
    return None


def _final_outcome(
    diagnosis: FailureDiagnosis,
    attempts: Sequence[RecoveryAttempt],
    successful_recovery_execution_id: str | None,
) -> FinalExecutionOutcome:
    if successful_recovery_execution_id is not None:
        return FinalExecutionOutcome.RECOVERED
    if diagnosis.partial_results_available or diagnosis.execution_status == "partially_completed":
        return FinalExecutionOutcome.PARTIALLY_COMPLETED
    if diagnosis.is_failure or diagnosis.execution_status == "failed" or attempts:
        return FinalExecutionOutcome.FAILED
    if diagnosis.execution_status == "completed":
        return FinalExecutionOutcome.COMPLETED
    return FinalExecutionOutcome.UNKNOWN


def _is_retryable_recovery_error(error_type: str | None) -> bool:
    return error_type in {"TimeoutError", "ConnectionError"}
