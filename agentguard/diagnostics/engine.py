"""Deterministic, evidence-backed execution failure diagnosis."""
from __future__ import annotations

from typing import Sequence

from agentguard.diagnostics.patterns import FailurePatternDetector, PatternConfig
from agentguard.diagnostics.types import (
    CATEGORY_ORDER,
    DiagnosticValidationError,
    Evidence,
    FailureCategory,
    FailureDiagnosis,
    FailurePattern,
    MLRiskSignal,
    PatternType,
)
from agentguard.schemas.events import EventType, ExecutionEvent


DIRECT_CATEGORIES = {
    FailureCategory.INVALID_ARGUMENTS,
    FailureCategory.TIMEOUT,
    FailureCategory.TOOL_UNAVAILABLE,
    FailureCategory.TOOL_FAILURE,
}


class FailureDiagnosticEngine:
    def __init__(
        self,
        detector: FailurePatternDetector | None = None,
        config: PatternConfig | None = None,
    ) -> None:
        self.detector = detector or FailurePatternDetector(config)

    def diagnose(
        self,
        events: Sequence[ExecutionEvent],
        *,
        ml_signal: MLRiskSignal | None = None,
    ) -> FailureDiagnosis:
        prefix = tuple(events)
        ml_signal = validate_ml_signal(ml_signal)
        patterns = self.detector.detect(prefix)
        by_type = {pattern.pattern_type: pattern for pattern in patterns}
        failures = tuple(event for event in prefix if event.event_type == EventType.TOOL_FAILED)
        successes = tuple(event for event in prefix if event.event_type == EventType.TOOL_COMPLETED)
        terminal = next(
            (event for event in prefix if event.event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED)),
            None,
        )
        terminal_failed = terminal is not None and terminal.event_type == EventType.EXECUTION_FAILED
        limit_signal = PatternType.EXECUTION_LIMIT_SIGNAL in by_type or any(_is_limit_event(event) for event in prefix)
        latest_failure = failures[-1] if failures else None

        primary, secondary = self._categories(
            failures=failures,
            terminal_failed=terminal_failed,
            limit_signal=limit_signal,
            by_type=by_type,
        )
        is_failure = bool(failures or terminal_failed or limit_signal)
        confidence, reason = self._confidence_and_reason(primary, is_failure, latest_failure)
        tool_name = self._tool_name(latest_failure, terminal, by_type)
        evidence = self._evidence(prefix, failures, patterns, ml_signal, primary)
        summary = self._summary(primary, is_failure, reason, ml_signal)

        return FailureDiagnosis(
            is_failure=is_failure,
            primary_category=primary,
            secondary_categories=secondary,
            confidence=confidence,
            summary=summary,
            execution_id=prefix[0].execution_id if prefix else None,
            task_id=prefix[0].task_id if prefix else None,
            tool_name=tool_name,
            terminal_observed=terminal is not None,
            partial_results_available=bool(successes),
            execution_status=(
                terminal.metadata.get("execution_status", terminal.status)
                if terminal is not None else None
            ),
            patterns=patterns,
            evidence=evidence,
            ml_risk=ml_signal,
        )

    def _categories(
        self,
        *,
        failures: Sequence[ExecutionEvent],
        terminal_failed: bool,
        limit_signal: bool,
        by_type: dict[PatternType, FailurePattern],
    ) -> tuple[FailureCategory, tuple[FailureCategory, ...]]:
        secondary: set[FailureCategory] = set()
        if limit_signal:
            primary = FailureCategory.EXECUTION_LIMIT_REACHED
        elif failures:
            latest_category = _classify_failure(failures[-1])
            if latest_category in DIRECT_CATEGORIES and latest_category != FailureCategory.TOOL_FAILURE:
                primary = latest_category
            elif (PatternType.REPEATED_TOOL_FAILURE in by_type
                  or PatternType.ALTERNATING_FAILURES in by_type
                  or PatternType.RETRIES_WITHOUT_PROGRESS in by_type):
                primary = FailureCategory.REPEATED_FAILURE
            else:
                primary = FailureCategory.TOOL_FAILURE
            if (PatternType.REPEATED_TOOL_FAILURE in by_type
                    or PatternType.ALTERNATING_FAILURES in by_type
                    or PatternType.RETRIES_WITHOUT_PROGRESS in by_type):
                secondary.add(FailureCategory.REPEATED_FAILURE)
            if PatternType.REPEATED_INVALID_ARGUMENTS in by_type:
                secondary.add(FailureCategory.INVALID_ARGUMENTS)
            if any(_is_timeout(event) for event in failures):
                secondary.add(FailureCategory.TIMEOUT)
            if any(_is_invalid_arguments(event) for event in failures):
                secondary.add(FailureCategory.INVALID_ARGUMENTS)
            if any(_is_unavailable(event) for event in failures):
                secondary.add(FailureCategory.TOOL_UNAVAILABLE)
        elif terminal_failed:
            if PatternType.POSSIBLE_LOOP in by_type:
                primary = FailureCategory.POSSIBLE_LOOP
            elif PatternType.REPEATED_ACTION in by_type:
                primary = FailureCategory.REPEATED_ACTION
            elif PatternType.EXCESSIVE_TOOL_CALLS in by_type:
                primary = FailureCategory.EXCESSIVE_USAGE
            else:
                primary = FailureCategory.INCOMPLETE_EXECUTION
        elif PatternType.POSSIBLE_LOOP in by_type:
            primary = FailureCategory.POSSIBLE_LOOP
        elif PatternType.REPEATED_ACTION in by_type:
            primary = FailureCategory.REPEATED_ACTION
        elif PatternType.EXCESSIVE_TOOL_CALLS in by_type:
            primary = FailureCategory.EXCESSIVE_USAGE
        else:
            primary = FailureCategory.UNKNOWN

        if PatternType.REPEATED_ACTION in by_type or PatternType.REPEATED_ARGUMENTS in by_type:
            secondary.add(FailureCategory.REPEATED_ACTION)
        if PatternType.POSSIBLE_LOOP in by_type:
            secondary.add(FailureCategory.POSSIBLE_LOOP)
        if PatternType.EXCESSIVE_TOOL_CALLS in by_type:
            secondary.add(FailureCategory.EXCESSIVE_USAGE)
        if PatternType.TIMEOUT_CLUSTER in by_type or PatternType.CONSECUTIVE_TIMEOUTS in by_type:
            secondary.add(FailureCategory.TIMEOUT)
        if PatternType.EXECUTION_LIMIT_SIGNAL in by_type:
            secondary.add(FailureCategory.EXECUTION_LIMIT_REACHED)
        if terminal_failed and not failures and primary == FailureCategory.INCOMPLETE_EXECUTION:
            secondary.discard(FailureCategory.INCOMPLETE_EXECUTION)
        secondary.discard(primary)
        ordered = tuple(category for category in CATEGORY_ORDER if category in secondary)
        return primary, ordered

    def _confidence_and_reason(
        self,
        primary: FailureCategory,
        is_failure: bool,
        latest_failure: ExecutionEvent | None,
    ) -> tuple[float, str]:
        if primary == FailureCategory.UNKNOWN:
            return (0.0, "No failure evidence was observed in the supplied event prefix")
        if primary in {FailureCategory.EXECUTION_LIMIT_REACHED, FailureCategory.INVALID_ARGUMENTS,
                       FailureCategory.TIMEOUT, FailureCategory.TOOL_UNAVAILABLE}:
            return (1.0, f"The execution reported {primary.value.replace('_', ' ')} evidence")
        if primary == FailureCategory.REPEATED_FAILURE:
            return (0.95, "Repeated tool failures met the configured pattern threshold")
        if primary in {FailureCategory.REPEATED_ACTION, FailureCategory.POSSIBLE_LOOP}:
            return (0.9 if is_failure else 0.75, "Repeated or cyclic tool selection met the configured pattern threshold")
        if primary == FailureCategory.EXCESSIVE_USAGE:
            return (0.85 if is_failure else 0.7, "Tool usage met the configured excessive-usage threshold")
        if primary == FailureCategory.INCOMPLETE_EXECUTION:
            return (0.9, "The execution terminated as failed without a more specific tool-failure signal")
        error_type = latest_failure.error_type if latest_failure is not None else "unknown"
        return (0.85, f"A recorded tool failure of type {error_type!r} was observed")

    def _tool_name(
        self,
        latest_failure: ExecutionEvent | None,
        terminal: ExecutionEvent | None,
        by_type: dict[PatternType, FailurePattern],
    ) -> str | None:
        if latest_failure is not None and latest_failure.tool_name:
            return latest_failure.tool_name
        if terminal is not None and terminal.tool_name:
            return terminal.tool_name
        for pattern_type in (PatternType.REPEATED_TOOL_FAILURE, PatternType.REPEATED_ACTION,
                             PatternType.POSSIBLE_LOOP, PatternType.TIMEOUT_CLUSTER):
            pattern = by_type.get(pattern_type)
            if pattern is not None and pattern.tool_name:
                return pattern.tool_name
        return None

    def _evidence(
        self,
        prefix: Sequence[ExecutionEvent],
        failures: Sequence[ExecutionEvent],
        patterns: Sequence[FailurePattern],
        ml_signal: MLRiskSignal | None,
        primary: FailureCategory,
    ) -> tuple[Evidence, ...]:
        evidence: list[Evidence] = []
        if failures:
            latest = failures[-1]
            evidence.append(Evidence(
                description=f"Latest tool failure was classified as {_classify_failure(latest).value}",
                source="tool_failed",
                event_ids=(latest.event_id,),
            ))
        for pattern in patterns:
            evidence.append(Evidence(
                description=pattern.description,
                source=f"pattern:{pattern.pattern_type.value}",
                event_ids=pattern.event_ids,
                observed_value=float(pattern.count),
                threshold=float(pattern.threshold) if pattern.threshold is not None else None,
            ))
        for event in prefix:
            if _is_limit_event(event):
                evidence.append(Evidence(
                    description="An event explicitly reported the tool-call limit",
                    source="execution_limit",
                    event_ids=(event.event_id,),
                ))
        if primary == FailureCategory.INCOMPLETE_EXECUTION:
            terminal = next(
                (event for event in reversed(prefix)
                 if event.event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED)),
                None,
            )
            evidence.append(Evidence(
                description="The execution ended as failed without a specific tool-failure diagnosis",
                source="terminal_event",
                event_ids=(terminal.event_id,) if terminal is not None else (),
            ))
        if ml_signal is not None:
            evidence.append(ml_signal.to_evidence())
        return tuple(evidence)

    def _summary(
        self,
        primary: FailureCategory,
        is_failure: bool,
        reason: str,
        ml_signal: MLRiskSignal | None,
    ) -> str:
        if is_failure:
            summary = f"{primary.value} detected: {reason}."
        elif primary == FailureCategory.UNKNOWN:
            summary = "No failure evidence was observed in the supplied event prefix."
        else:
            summary = f"{primary.value} risk signal observed, but no execution failure is asserted: {reason}."
        if ml_signal is not None and ml_signal.predicted_failure:
            summary += (
                f" ML failure probability {ml_signal.probability:.4f} exceeds threshold "
                f"{ml_signal.threshold:.4f}; this is predictive risk, not confirmed failure."
            )
        elif ml_signal is not None:
            summary += (
                f" ML failure probability {ml_signal.probability:.4f} is below threshold "
                f"{ml_signal.threshold:.4f}."
            )
        return summary


def _classify_failure(event: ExecutionEvent) -> FailureCategory:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    if _is_limit_event(event):
        return FailureCategory.EXECUTION_LIMIT_REACHED
    if _is_invalid_arguments(event):
        return FailureCategory.INVALID_ARGUMENTS
    if _is_timeout(event):
        return FailureCategory.TIMEOUT
    if _is_unavailable(event):
        return FailureCategory.TOOL_UNAVAILABLE
    return FailureCategory.TOOL_FAILURE


def _is_timeout(event: ExecutionEvent) -> bool:
    return event.error_type == "TimeoutError" or "timeout" in (event.error_message or "").lower()


def _is_invalid_arguments(event: ExecutionEvent) -> bool:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    return (
        event.error_type in {"ValidationError", "ToolInputValidationError"}
        or "invalid argument" in text
        or "invalid_arguments" in text
        or "tool input validation failed" in text
    )


def _is_unavailable(event: ExecutionEvent) -> bool:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    return (
        event.error_type == "ConnectionError"
        or "unavailable" in text
    )


def _is_limit_event(event: ExecutionEvent) -> bool:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    return (
        "toolcalllimitexceeded" in text
        or "maximum tool call limit" in text
        or ("maximum of" in text and "tool call" in text)
    )


def validate_ml_signal(value: object) -> MLRiskSignal | None:
    if value is None:
        return None
    if isinstance(value, MLRiskSignal):
        # Revalidate even model_construct()/deserialized instances before using them.
        return MLRiskSignal.model_validate(value.model_dump())
    raise DiagnosticValidationError("ml_signal must be an MLRiskSignal or None")
