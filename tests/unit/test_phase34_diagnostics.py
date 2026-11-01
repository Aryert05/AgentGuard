from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agentguard.database.models import RecoveryAttemptBudgetRecord, RecoveryAttemptRecord, RecoverySessionRecord
from agentguard.database.repositories import TelemetryPersistenceError, persist_recovery_session
from agentguard.diagnostics import (
    BoundedRecoveryController,
    DiagnosticService,
    DiagnosticValidationError,
    FailureCategory,
    FailureDiagnosticEngine,
    FailurePatternDetector,
    InMemoryRecoveryTelemetry,
    MLRiskSignal,
    PatternConfig,
    PatternType,
    RecoveryAction,
    RecoveryOutcome,
    RecoveryPolicy,
    RecoveryRecommendation,
    RecoveryRecommender,
    RecoveryRisk,
    RecoveryStatus,
    InMemoryRecoveryAttemptLedger,
    RecoveryAttempt,
    RecoveryAttemptStatus,
)
from agentguard.diagnostics.recovery import RecoveryExecutionError
from agentguard.execution.runner import run_plan
from agentguard.schemas.events import EventType, ExecutionEvent
from agentguard.telemetry.privacy import fingerprint_tool_arguments
from agentguard.telemetry.persistence import DatabaseRecoveryTelemetry


BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


def event(
    number: int,
    kind: EventType,
    *,
    tool: str | None = None,
    status: str = "running",
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"evt-{number}",
        execution_id="execution-1",
        task_id="task-1",
        timestamp=BASE_TIME + timedelta(milliseconds=number),
        step_number=number,
        event_type=kind,
        component="tool" if tool else "agent",
        tool_name=tool,
        status=status,
        error_type=error_type,
        error_message=error_message,
        metadata=metadata or {},
    )


def start() -> list[ExecutionEvent]:
    return [event(0, EventType.EXECUTION_STARTED, status="running")]


def selected(number: int, tool: str, fingerprint: str | None = None) -> ExecutionEvent:
    metadata = {"argument_names": ["query"]}
    if fingerprint:
        metadata["arguments_fingerprint"] = fingerprint
    return event(number, EventType.TOOL_SELECTED, tool=tool, status="selected", metadata=metadata)


def failed(number: int, tool: str, error_type: str = "RuntimeError", message: str = "tool failed") -> ExecutionEvent:
    return event(number, EventType.TOOL_FAILED, tool=tool, status="failed", error_type=error_type, error_message=message)


def recommendation(
    action: RecoveryAction = RecoveryAction.RETRY_TOOL,
    *,
    tool: str | None = "search",
    approval: bool = False,
    attempts: int = 1,
    risk: RecoveryRisk = RecoveryRisk.LOW,
) -> RecoveryRecommendation:
    return RecoveryRecommendation(
        action=action,
        diagnosis_category=FailureCategory.TIMEOUT,
        target_tool=tool,
        risk=risk,
        requires_human_approval=approval,
        max_attempts=attempts,
        rationale="Test recommendation with bounded action.",
        expected_benefit="A controlled recovery may complete the task.",
        evidence_event_ids=("evt-2",),
    )


class ScriptedExecutor:
    def __init__(self, results: list[bool]) -> None:
        self.results = list(results)
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        result = self.results.pop(0) if self.results else False
        return RecoveryOutcome(
            success=result,
            output="mock success" if result else None,
            error_type=None if result else "TimeoutError",
            error_message=None if result else "simulated timeout",
        )


def test_direct_failure_categories_and_supporting_evidence():
    cases = [
        ("ValidationError", "Tool input validation failed", FailureCategory.INVALID_ARGUMENTS),
        ("TimeoutError", "Simulated tool timeout", FailureCategory.TIMEOUT),
        ("ConnectionError", "Simulated tool unavailability", FailureCategory.TOOL_UNAVAILABLE),
        ("RuntimeError", "Unexpected tool exception", FailureCategory.TOOL_FAILURE),
    ]
    for index, (error_type, message, expected) in enumerate(cases, start=1):
        trace = start() + [selected(index, "calculator"), failed(index + 1, "calculator", error_type, message)]
        diagnosis = FailureDiagnosticEngine().diagnose(trace)
        assert diagnosis.is_failure is True
        assert diagnosis.primary_category == expected
        assert diagnosis.tool_name == "calculator"
        assert diagnosis.evidence[0].event_ids == (f"evt-{index + 1}",)

    unknown_tool = start() + [selected(1, "invented_tool"),
                              failed(2, "invented_tool", "LookupError", "Unknown tool: invented_tool")]
    report = DiagnosticService().analyze(unknown_tool)
    assert report.diagnosis.primary_category == FailureCategory.TOOL_FAILURE
    assert report.recommendations[0].action == RecoveryAction.REQUEST_HUMAN_REVIEW


def test_repeated_failures_and_alternating_tool_failures_are_explained():
    repeated = start()
    for number in range(1, 4):
        repeated.extend([selected(number * 2 - 1, "search"), failed(number * 2, "search")])
    diagnosis = FailureDiagnosticEngine().diagnose(repeated)
    assert diagnosis.primary_category == FailureCategory.REPEATED_FAILURE
    pattern = next(item for item in diagnosis.patterns if item.pattern_type == PatternType.REPEATED_TOOL_FAILURE)
    assert pattern.count == 3
    assert pattern.event_ids == ("evt-2", "evt-4", "evt-6")
    assert all(item.action != RecoveryAction.RETRY_TOOL for item in DiagnosticService().analyze(repeated).recommendations)
    assert any(item.pattern_type == PatternType.RETRIES_WITHOUT_PROGRESS for item in diagnosis.patterns)

    alternating = start()
    for number, tool in enumerate(("search", "company_data", "search", "company_data"), start=1):
        alternating.extend([selected(number * 2 - 1, tool), failed(number * 2, tool)])
    diagnosis = FailureDiagnosticEngine().diagnose(alternating)
    pattern = next(item for item in diagnosis.patterns if item.pattern_type == PatternType.ALTERNATING_FAILURES)
    assert pattern.count == 4
    assert len(pattern.event_ids) == 4
    assert diagnosis.primary_category == FailureCategory.REPEATED_FAILURE
    assert FailureCategory.POSSIBLE_LOOP in diagnosis.secondary_categories


def test_repeated_argument_fingerprints_are_detected_without_logging_arguments():
    trace = start() + [selected(1, "search", "digest-a"), selected(2, "search", "digest-a"),
                       selected(3, "search", "digest-b"), selected(4, "search", "digest-a")]
    patterns = FailurePatternDetector(PatternConfig(repeated_action_threshold=10)).detect(trace)
    repeated = next(item for item in patterns if item.pattern_type == PatternType.REPEATED_ARGUMENTS)
    assert repeated.count == 3
    assert repeated.event_ids == ("evt-1", "evt-2", "evt-4")
    assert "digest-a" not in str(repeated.model_dump())

    invalid = start()
    for number in range(1, 4):
        invalid.extend([selected(number * 2 - 1, "calculator"),
                        failed(number * 2, "calculator", "ValidationError", "invalid arguments")])
    patterns = FailurePatternDetector().detect(invalid)
    repeated_invalid = next(p for p in patterns if p.pattern_type == PatternType.REPEATED_INVALID_ARGUMENTS)
    assert repeated_invalid.count == 3
    assert repeated_invalid.event_ids == ("evt-2", "evt-4", "evt-6")


def test_live_offline_tool_selection_records_only_deterministic_argument_fingerprint():
    args = {"operation": "add", "a": 51, "b": 17}
    _, collector = run_plan(
        "deterministic fingerprint test",
        plan=[("calculator", args)],
        deterministic_event_ids=True,
    )
    selection = next(item for item in collector.events if item.event_type == EventType.TOOL_SELECTED)
    assert selection.metadata["arguments_fingerprint"] == fingerprint_tool_arguments(args)
    assert selection.metadata["argument_names"] == ["a", "b", "operation"]
    assert all(not isinstance(value, int) for value in selection.metadata.values())
    assert fingerprint_tool_arguments(args) == fingerprint_tool_arguments(dict(reversed(list(args.items()))))
    assert fingerprint_tool_arguments(args) != fingerprint_tool_arguments({**args, "b": 18})


def test_timeout_clusters_require_multiple_failures_and_consecutive_rule_is_prefix_only():
    isolated = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    assert not FailurePatternDetector().detect(isolated)

    trace = isolated + [selected(3, "search"), failed(4, "search", "TimeoutError", "timeout")]
    prefix_diagnosis = FailureDiagnosticEngine().diagnose(trace[:3])
    assert all(p.pattern_type != PatternType.CONSECUTIVE_TIMEOUTS for p in prefix_diagnosis.patterns)
    full_diagnosis = FailureDiagnosticEngine().diagnose(trace)
    pattern = next(p for p in full_diagnosis.patterns if p.pattern_type == PatternType.CONSECUTIVE_TIMEOUTS)
    assert pattern.event_ids == ("evt-2", "evt-4")


def test_repeated_action_loop_excessive_use_limit_incomplete_and_unknown():
    repeated = start() + [selected(n, "search") for n in (1, 2, 3)]
    diagnosis = FailureDiagnosticEngine().diagnose(repeated)
    assert diagnosis.primary_category == FailureCategory.REPEATED_ACTION
    assert diagnosis.is_failure is False  # pattern/risk is not a confirmed terminal failure
    recommendation = DiagnosticService().analyze(repeated).recommendations[0]
    assert recommendation.action == RecoveryAction.REQUEST_HUMAN_REVIEW
    assert recommendation.requires_human_approval
    executor = ScriptedExecutor([True])
    controller = BoundedRecoveryController()
    session = controller.recover(diagnosis, [recommendation], executor)
    assert session.status == RecoveryStatus.NO_ACTION
    assert executor.requests == []
    assert controller.telemetry.sessions == [session]

    loop = start() + [selected(n, tool) for n, tool in enumerate(("search", "company_data") * 2, start=1)]
    diagnosis = FailureDiagnosticEngine().diagnose(loop)
    assert diagnosis.primary_category == FailureCategory.POSSIBLE_LOOP
    assert diagnosis.is_failure is False

    excessive_detector = FailurePatternDetector(PatternConfig(repeated_action_threshold=10, loop_cycle_lengths=(5,)))
    excessive = start() + [selected(n, tool) for n, tool in enumerate(
        ("search", "company_data", "calculator", "search", "company_data", "calculator"), start=1)]
    assert FailureDiagnosticEngine(excessive_detector).diagnose(excessive).primary_category == FailureCategory.EXCESSIVE_USAGE

    limited = start() + [event(1, EventType.EXECUTION_FAILED, status="failed",
                               error_type="ToolCallLimitExceeded", error_message="Maximum tool call limit reached")]
    assert FailureDiagnosticEngine().diagnose(limited).primary_category == FailureCategory.EXECUTION_LIMIT_REACHED

    incomplete = start() + [event(1, EventType.TOOL_COMPLETED, tool="search", status="completed"),
                            event(2, EventType.EXECUTION_FAILED, status="failed", error_type="ExecutionIncomplete")]
    diagnosis = FailureDiagnosticEngine().diagnose(incomplete)
    assert diagnosis.primary_category == FailureCategory.INCOMPLETE_EXECUTION
    assert diagnosis.partial_results_available is True
    assert diagnosis.execution_status == "failed"
    assert FailureDiagnosticEngine().diagnose(start()).primary_category == FailureCategory.UNKNOWN


def test_pattern_validation_rejects_mixed_executions_and_events_after_terminal():
    valid = start() + [event(1, EventType.EXECUTION_COMPLETED, status="completed")]
    with pytest.raises(DiagnosticValidationError, match="after a terminal"):
        FailurePatternDetector().detect(valid + [selected(2, "search")])
    mixed = start() + [event(1, EventType.TOOL_FAILED, tool="search", status="failed")]
    mixed.append(ExecutionEvent(
        event_id="other", execution_id="execution-2", task_id="task-1", timestamp=BASE_TIME + timedelta(seconds=1),
        event_type=EventType.TOOL_FAILED, component="tool", tool_name="search", status="failed",
    ))
    with pytest.raises(DiagnosticValidationError, match="mixes execution"):
        FailurePatternDetector().detect(mixed)


def test_ml_risk_is_predictive_only_and_rule_diagnosis_survives_missing_or_bad_scorer():
    high = DiagnosticService().analyze(start(), ml_signal=MLRiskSignal(probability=0.91, threshold=0.8))
    assert high.diagnosis.is_failure is False
    assert high.diagnosis.primary_category == FailureCategory.UNKNOWN
    assert high.recommendations[0].requires_human_approval is True
    assert "not confirmed" in high.diagnosis.summary.lower() or "predict" in high.diagnosis.summary.lower()

    low_with_failure = DiagnosticService().analyze(
        start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")],
        ml_signal=MLRiskSignal(probability=0.1, threshold=0.8),
    )
    assert low_with_failure.diagnosis.is_failure is True
    assert low_with_failure.diagnosis.primary_category == FailureCategory.TIMEOUT

    class MissingModel:
        def score(self, events):
            raise FileNotFoundError("optional model bundle unavailable")

    fallback = DiagnosticService(ml_scorer=MissingModel()).analyze(
        start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    )
    assert fallback.diagnosis.primary_category == FailureCategory.TIMEOUT
    assert fallback.diagnosis.ml_risk is None
    assert "FileNotFoundError" in fallback.ml_error

    with pytest.raises(ValidationError):
        MLRiskSignal(probability=1.2)
    with pytest.raises(ValidationError):
        MLRiskSignal.model_validate({"threshold": 0.5})
    with pytest.raises(ValidationError):
        MLRiskSignal(probability=float("nan"))
    with pytest.raises(DiagnosticValidationError, match="MLRiskSignal"):
        FailureDiagnosticEngine().diagnose(start(), ml_signal={"probability": 0.5})


def test_bounded_retry_success_records_attempt_ids_trigger_and_original_execution():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    rec = recommendation(attempts=2)
    executor = ScriptedExecutor([False, True])
    telemetry = InMemoryRecoveryTelemetry()
    seq = iter(f"id-{n}" for n in range(1, 10))
    clock_seq = iter(BASE_TIME + timedelta(seconds=n) for n in range(10))
    delays = []
    controller = BoundedRecoveryController(
        RecoveryPolicy(max_total_attempts=2, max_attempts_per_recommendation=2),
        clock=lambda: next(clock_seq), id_factory=lambda: next(seq), telemetry=telemetry,
        sleeper=delays.append,
    )
    session = controller.recover(diagnosis, [rec], executor, original_execution_id="execution-1")
    assert session.status == RecoveryStatus.RECOVERED
    assert session.final_execution_outcome.value == "recovered"
    assert [attempt.attempt_number for attempt in session.attempts] == [1, 2]
    assert all(attempt.original_execution_id == "execution-1" for attempt in session.attempts)
    assert all(attempt.triggering_event_id == "evt-2" for attempt in session.attempts)
    assert [request.tool_call_budget for request in executor.requests] == [1, 1]
    assert [request.backoff_seconds for request in executor.requests] == [0.1, 0.25]
    assert delays == [0.1, 0.25]
    assert all(request.chain_depth == 1 for request in executor.requests)
    assert len(telemetry.sessions) == 1


def test_recovery_exhaustion_is_globally_bounded_and_marks_partial_completion():
    trace = start() + [event(1, EventType.TOOL_COMPLETED, tool="search", status="completed"),
                       selected(2, "search"), failed(3, "search", "TimeoutError", "timeout"),
                       event(4, EventType.EXECUTION_FAILED, status="failed", error_type="ToolExecutionError",
                             metadata={"execution_status": "partially_completed"})]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    executor = ScriptedExecutor([False, False, False])
    controller = BoundedRecoveryController(RecoveryPolicy(max_total_attempts=2, max_attempts_per_recommendation=3))
    rec = recommendation(attempts=5).model_copy(update={"evidence_event_ids": ("evt-3",)})
    session = controller.recover(diagnosis, [rec], executor)
    assert session.status == RecoveryStatus.EXHAUSTED
    assert len(session.attempts) == 2
    assert len(executor.requests) == 2
    assert [attempt.attempt_number for attempt in session.attempts] == [1, 2]
    assert session.final_execution_outcome.value == "partially_completed"


def test_permanent_recovery_error_stops_even_when_retry_budget_remains():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)

    class PermanentExecutor:
        def __init__(self):
            self.calls = 0

        def execute(self, request):
            self.calls += 1
            return RecoveryOutcome(success=False, error_type="ValueError", error_message="permanent failure")

    executor = PermanentExecutor()
    session = BoundedRecoveryController(
        RecoveryPolicy(max_total_attempts=4, max_attempts_per_recommendation=3)
    ).recover(diagnosis, [recommendation(attempts=4)], executor)
    assert executor.calls == 1
    assert len(session.attempts) == 1
    assert any("permanent or unclassified" in reason for reason in session.blocked_reasons)


def test_per_execution_budget_prevents_second_session_from_retrying():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    controller = BoundedRecoveryController(RecoveryPolicy(max_total_attempts=1))
    first_executor = ScriptedExecutor([False])
    first = controller.recover(diagnosis, [recommendation()], first_executor)
    assert first.status == RecoveryStatus.EXHAUSTED
    second_executor = ScriptedExecutor([True])
    second = controller.recover(diagnosis, [recommendation()], second_executor)
    assert second.status == RecoveryStatus.BLOCKED
    assert second.attempts == ()
    assert second_executor.requests == []
    assert any("Per-execution recovery-attempt limit" in reason for reason in second.blocked_reasons)


def test_permanent_unknown_high_risk_and_unavailable_without_alternative_do_not_fabricate_actions():
    generic = start() + [selected(1, "search"), failed(2, "search", "ValueError", "permanent input error")]
    diagnosis = FailureDiagnosticEngine().diagnose(generic)
    recs = DiagnosticService().analyze(generic).recommendations
    assert recs[0].action == RecoveryAction.REQUEST_HUMAN_REVIEW
    executor = ScriptedExecutor([True])
    session = BoundedRecoveryController().recover(diagnosis, recs, executor)
    assert session.attempts == ()
    assert session.blocked_reasons
    assert executor.requests == []

    invalid = start() + [selected(1, "calculator"), failed(2, "calculator", "ValidationError", "invalid arguments")]
    report = DiagnosticService().analyze(invalid)
    assert report.recommendations[0].requires_human_approval is True
    session = BoundedRecoveryController().recover(report.diagnosis, report.recommendations, executor)
    assert session.attempts == ()

    unavailable = start() + [selected(1, "search"), failed(2, "search", "ConnectionError", "unavailable")]
    report = DiagnosticService().analyze(unavailable)
    assert all(item.action != RecoveryAction.SWITCH_TOOL for item in report.recommendations)
    assert all(item.target_tool == "search" for item in report.recommendations)


def test_configured_alternative_is_used_only_after_bounded_unavailability_retry():
    unavailable = start() + [selected(1, "search"), failed(2, "search", "ConnectionError", "unavailable")]
    report = DiagnosticService(
        recommender=RecoveryRecommender(alternatives={"search": ("company_data",)})
    ).analyze(unavailable)
    assert [item.action for item in report.recommendations] == [RecoveryAction.RETRY_TOOL, RecoveryAction.SWITCH_TOOL]
    executor = ScriptedExecutor([False, True])
    session = BoundedRecoveryController().recover(report.diagnosis, report.recommendations, executor)
    assert session.status == RecoveryStatus.RECOVERED
    assert [item.action for item in session.attempts] == [RecoveryAction.RETRY_TOOL, RecoveryAction.SWITCH_TOOL]
    assert [item.attempt_number for item in session.attempts] == [1, 2]


def test_execution_identity_and_approval_gates_cannot_be_bypassed():
    trace = start() + [event(1, EventType.EXECUTION_FAILED, status="failed", error_type="ExecutionIncomplete")]
    report = DiagnosticService().analyze(trace)
    assert report.recommendations[0].requires_human_approval
    executor = ScriptedExecutor([True])
    controller = BoundedRecoveryController()
    session = controller.recover(report.diagnosis, report.recommendations, executor)
    assert session.attempts == ()
    assert executor.requests == []
    with pytest.raises(DiagnosticValidationError, match="must match"):
        controller.recover(report.diagnosis, report.recommendations, executor, original_execution_id="other")


def test_recursive_or_concurrent_recovery_is_rejected():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    controller = BoundedRecoveryController()

    class RecursiveExecutor:
        def execute(self, request):
            with pytest.raises(RecoveryExecutionError):
                controller.recover(diagnosis, [recommendation()], ScriptedExecutor([True]))
            return RecoveryOutcome(success=True)

    session = controller.recover(diagnosis, [recommendation()], RecursiveExecutor())
    assert session.status == RecoveryStatus.RECOVERED


def test_original_execution_limit_and_recovery_schema_contracts():
    assert "triggering_event_id" in RecoveryAttemptRecord.__table__.c
    assert "diagnosis_category" in RecoveryAttemptRecord.__table__.c
    assert "actual_action" in RecoveryAttemptRecord.__table__.c
    assert "final_execution_outcome" in RecoverySessionRecord.__table__.c
    assert "attempt_count" in RecoveryAttemptBudgetRecord.__table__.c
    recovery_attempt_constraints = {
        constraint.name for constraint in RecoveryAttemptRecord.__table__.constraints
    }
    assert "ck_recovery_attempts_time_order" in recovery_attempt_constraints
    assert "ck_recovery_attempts_duration_within_timestamps" in recovery_attempt_constraints
    with pytest.raises(ValidationError):
        RecoveryPolicy(max_total_attempts=6)
    with pytest.raises(ValidationError):
        RecoveryPolicy(approved_tool_names=("search",), retry_safe_tool_names=("calculator",))


def test_database_telemetry_adapter_claims_attempts_without_connecting_in_unit_test():
    from unittest.mock import patch

    adapter = DatabaseRecoveryTelemetry(object())
    with patch("agentguard.database.repositories.claim_recovery_attempt_budget", return_value=True) as claim:
        assert adapter.claim("execution-1", 2) is True
    claim.assert_called_once_with(adapter.session_factory, "execution-1", 2)


def test_recovery_outcome_text_is_sanitized_before_returning_session():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)

    class SensitiveExecutor:
        def execute(self, request):
            return RecoveryOutcome(success=True, output="password=hunter2", metadata={"api_key": "secret-value"})

    session = BoundedRecoveryController().recover(
        diagnosis, [recommendation()], SensitiveExecutor(),
    )
    assert session.attempts[0].output == "password=[REDACTED]"
    assert session.attempts[0].metadata["api_key"] == "[REDACTED]"


def test_recovery_trigger_ids_must_be_bound_to_diagnosis_evidence():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout"),
                       failed(3, "search", "TimeoutError", "timeout"),
                       failed(4, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    valid = recommendation(attempts=1).model_copy(
        update={"evidence_event_ids": ("evt-2", "evt-4")}
    )

    class CaptureExecutor:
        def __init__(self):
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            return RecoveryOutcome(success=True)

    executor = CaptureExecutor()
    session = BoundedRecoveryController(sleeper=lambda _: None).recover(diagnosis, [valid], executor)
    assert executor.requests[0].triggering_event_id == "evt-2"
    assert session.attempts[0].triggering_event_id == "evt-2"

    for invalid_ids in (("evt-2", "forged-event"), (" ",)):
        forged = valid.model_copy(update={"evidence_event_ids": invalid_ids})
        rejected_executor = CaptureExecutor()
        with pytest.raises(DiagnosticValidationError, match="belong to diagnosis evidence"):
            BoundedRecoveryController(sleeper=lambda _: None).recover(
                diagnosis, [forged], rejected_executor,
            )
        assert rejected_executor.requests == []

    no_evidence_diagnosis = diagnosis.model_copy(update={"evidence": ()})
    no_evidence_recommendation = valid.model_copy(update={"evidence_event_ids": ()})
    missing_executor = CaptureExecutor()
    blocked = BoundedRecoveryController(sleeper=lambda _: None).recover(
        no_evidence_diagnosis, [no_evidence_recommendation], missing_executor,
    )
    assert blocked.status == RecoveryStatus.BLOCKED
    assert blocked.attempts == ()
    assert missing_executor.requests == []


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        (EventType.EXECUTION_COMPLETED, "failed"),
        (EventType.EXECUTION_FAILED, "completed"),
    ],
)
def test_diagnosis_rejects_contradictory_terminal_event_status(kind, status):
    with pytest.raises(DiagnosticValidationError, match="must have status"):
        FailureDiagnosticEngine().diagnose(start() + [event(1, kind, status=status)])


def test_diagnosis_accepts_consistent_terminal_events_and_preserves_partial_status():
    completed = FailureDiagnosticEngine().diagnose(
        start() + [event(1, EventType.EXECUTION_COMPLETED, status="completed")]
    )
    assert completed.is_failure is False
    assert completed.execution_status == "completed"

    partial = FailureDiagnosticEngine().diagnose(
        start() + [event(1, EventType.EXECUTION_FAILED, status="failed",
                         metadata={"execution_status": "partially_completed"})]
    )
    assert partial.is_failure is True
    assert partial.execution_status == "partially_completed"


def test_diagnosis_prefix_requires_one_start_first_but_allows_empty_and_streaming_prefixes():
    engine = FailureDiagnosticEngine()
    assert engine.diagnose([]).primary_category == FailureCategory.UNKNOWN

    with pytest.raises(DiagnosticValidationError, match="begin with execution_started"):
        engine.diagnose([selected(1, "search")])
    with pytest.raises(DiagnosticValidationError, match="exactly one execution_started"):
        engine.diagnose(start() + [event(1, EventType.EXECUTION_STARTED, status="running")])
    with pytest.raises(DiagnosticValidationError, match="after a terminal"):
        engine.diagnose(start() + [
            event(1, EventType.EXECUTION_COMPLETED, status="completed"),
            event(2, EventType.EXECUTION_FAILED, status="failed"),
        ])

    streaming = engine.diagnose(start() + [selected(1, "search")])
    assert streaming.is_failure is False
    assert streaming.terminal_observed is False


def test_repeated_invalid_argument_threshold_uses_its_own_configuration():
    detector = FailurePatternDetector(PatternConfig(
        repeated_error_threshold=2,
        repeated_arguments_threshold=4,
    ))
    for count in (3, 4, 5):
        trace = start() + [
            failed(index, "calculator", "ValidationError", "invalid argument")
            for index in range(1, count + 1)
        ]
        patterns = detector.detect(trace)
        invalid_pattern = next(
            (p for p in patterns if p.pattern_type == PatternType.REPEATED_INVALID_ARGUMENTS),
            None,
        )
        if count == 3:
            assert invalid_pattern is None
        else:
            assert invalid_pattern is not None
            assert invalid_pattern.count == count
            assert invalid_pattern.threshold == 4


@pytest.mark.parametrize(
    "scored_value",
    [
        {"probability": 0.9},
        0.9,
        object(),
        None,
        MLRiskSignal.model_construct(probability=float("nan"), threshold=0.5),
        MLRiskSignal.model_construct(probability=float("inf"), threshold=0.5),
        MLRiskSignal.model_construct(probability=-0.1, threshold=0.5),
        MLRiskSignal.model_construct(probability=1.1, threshold=0.5),
    ],
)
def test_invalid_ml_scorer_output_falls_back_to_rule_diagnosis(scored_value):
    class InvalidOutputScorer:
        def score(self, events):
            return scored_value

    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    report = DiagnosticService(ml_scorer=InvalidOutputScorer()).analyze(trace)
    assert report.diagnosis.primary_category == FailureCategory.TIMEOUT
    assert report.diagnosis.ml_risk is None
    assert report.ml_error


def test_engine_errors_outside_ml_scorer_boundary_are_not_swallowed():
    class ValidScorer:
        def score(self, events):
            return MLRiskSignal(probability=0.2)

    class BrokenEngine:
        def diagnose(self, events, *, ml_signal=None):
            raise RuntimeError("diagnosis bug")

    with pytest.raises(RuntimeError, match="diagnosis bug"):
        DiagnosticService(engine=BrokenEngine(), ml_scorer=ValidScorer()).analyze(start())


def test_shared_in_memory_ledger_coordinates_multiple_controllers():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    ledger = InMemoryRecoveryAttemptLedger()
    policy = RecoveryPolicy(max_total_attempts=1)

    first_executor = ScriptedExecutor([False])
    first = BoundedRecoveryController(policy, attempt_ledger=ledger, sleeper=lambda _: None).recover(
        diagnosis, [recommendation()], first_executor,
    )
    second_executor = ScriptedExecutor([True])
    second = BoundedRecoveryController(policy, attempt_ledger=ledger, sleeper=lambda _: None).recover(
        diagnosis, [recommendation()], second_executor,
    )
    assert len(first.attempts) == 1
    assert second.status == RecoveryStatus.BLOCKED
    assert second.attempts == ()
    assert second_executor.requests == []

    # The default ledger is intentionally controller-local.
    isolated_calls = []

    class IsolatedExecutor:
        def execute(self, request):
            isolated_calls.append(request)
            return RecoveryOutcome(success=True)

    for _ in range(2):
        BoundedRecoveryController(policy, sleeper=lambda _: None).recover(
            diagnosis, [recommendation()], IsolatedExecutor(),
        )
    assert len(isolated_calls) == 2


def test_recovery_attempt_rejects_backwards_clock_timestamps():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    times = iter((BASE_TIME, BASE_TIME + timedelta(seconds=2),
                  BASE_TIME + timedelta(seconds=1)))
    executor = ScriptedExecutor([True])
    telemetry = InMemoryRecoveryTelemetry()
    controller = BoundedRecoveryController(
        clock=lambda: next(times), sleeper=lambda _: None, telemetry=telemetry,
    )
    with pytest.raises(DiagnosticValidationError, match="precedes its start"):
        controller.recover(diagnosis, [recommendation()], executor)
    assert len(executor.requests) == 1
    assert telemetry.sessions == []

    with pytest.raises(ValidationError, match="completion must not precede its start"):
        RecoveryAttempt(
            attempt_id="a", session_id="s", original_execution_id="execution-1",
            triggering_event_id="evt-2", diagnosis_category=FailureCategory.TIMEOUT,
            recovery_execution_id="r", attempt_number=1, action=RecoveryAction.RETRY_TOOL,
            actual_action=RecoveryAction.RETRY_TOOL, risk=RecoveryRisk.LOW,
            status=RecoveryAttemptStatus.SUCCEEDED,
            started_at=BASE_TIME + timedelta(seconds=1), completed_at=BASE_TIME,
        )


def test_persist_recovery_session_validates_attempt_trigger_before_transaction():
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    session = BoundedRecoveryController(sleeper=lambda _: None).recover(
        diagnosis, [recommendation()], ScriptedExecutor([True]),
    )
    valid_attempt = session.attempts[0]

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class FakeSession:
        def __init__(self, attempt_id):
            self.attempt_id = attempt_id
            self.attempt_statement_params = None

        def execute(self, statement):
            return None

        def scalars(self, statement):
            self.attempt_statement_params = statement.compile().params
            return FakeResult([self.attempt_id])

    class FakeFactory:
        def __init__(self, attempt_id):
            self.begin_calls = 0
            self.session = FakeSession(attempt_id)

        def begin(self):
            self.begin_calls += 1
            return self

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, traceback):
            return False

    for bad_trigger in (None, "", " ", "forged-event", "evt-from-another-execution"):
        bad_attempt = valid_attempt.model_copy(update={"triggering_event_id": bad_trigger})
        bad_session = session.model_copy(update={"attempts": (bad_attempt,)})
        fake_factory = FakeFactory(valid_attempt.attempt_id)
        with pytest.raises(TelemetryPersistenceError, match="triggering event"):
            persist_recovery_session(fake_factory, bad_session)
        assert fake_factory.begin_calls == 0

    valid_factory = FakeFactory(valid_attempt.attempt_id)
    persist_recovery_session(valid_factory, session)
    assert valid_factory.begin_calls == 1
    assert valid_attempt.triggering_event_id in valid_factory.session.attempt_statement_params.values()

    elapsed_ms = (valid_attempt.completed_at - valid_attempt.started_at).total_seconds() * 1000
    impossible_duration = valid_attempt.model_copy(update={"duration_ms": elapsed_ms + 1})
    bad_duration_session = session.model_copy(update={"attempts": (impossible_duration,)})
    duration_factory = FakeFactory(valid_attempt.attempt_id)
    with pytest.raises(TelemetryPersistenceError, match="duration is inconsistent"):
        persist_recovery_session(duration_factory, bad_duration_session)
    assert duration_factory.begin_calls == 0


@pytest.mark.parametrize(
    ("elapsed_seconds", "reported_duration_ms", "should_raise"),
    [(0, 0, False), (1, 900, False), (1, None, False), (1, 1001, True)],
)
def test_duration_contract_equal_forward_and_mismatched_values(
    elapsed_seconds, reported_duration_ms, should_raise,
):
    trace = start() + [selected(1, "search"), failed(2, "search", "TimeoutError", "timeout")]
    diagnosis = FailureDiagnosticEngine().diagnose(trace)
    with pytest.raises(ValidationError):
        RecoveryOutcome(success=False, duration_ms=-1)
    with pytest.raises(ValidationError):
        RecoveryOutcome(success=False, duration_ms=float("inf"))
    completed_at = BASE_TIME + timedelta(seconds=elapsed_seconds)
    clock_values = iter((BASE_TIME, BASE_TIME, completed_at, completed_at))

    class DurationExecutor:
        def execute(self, request):
            return RecoveryOutcome(success=True, duration_ms=reported_duration_ms)

    controller = BoundedRecoveryController(
        clock=lambda: next(clock_values), sleeper=lambda _: None,
    )
    if should_raise:
        with pytest.raises(DiagnosticValidationError, match="exceeds the attempt timestamp envelope"):
            controller.recover(diagnosis, [recommendation()], DurationExecutor())
    else:
        session = controller.recover(diagnosis, [recommendation()], DurationExecutor())
        assert session.attempts[0].started_at == BASE_TIME
        assert session.attempts[0].completed_at == completed_at
        expected_duration = (
            reported_duration_ms if reported_duration_ms is not None else elapsed_seconds * 1000
        )
        assert session.attempts[0].duration_ms == expected_duration
