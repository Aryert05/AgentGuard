"""Prefix-safe deterministic failure-pattern detection."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from agentguard.diagnostics.types import (
    DiagnosticValidationError,
    FailurePattern,
    PatternSeverity,
    PatternType,
)
from agentguard.schemas.events import EventType, ExecutionEvent


@dataclass(frozen=True)
class PatternConfig:
    repeated_failure_threshold: int = 3
    repeated_error_threshold: int = 3
    consecutive_failure_threshold: int = 3
    repeated_action_threshold: int = 3
    repeated_arguments_threshold: int = 3
    alternating_failure_threshold: int = 4
    timeout_cluster_threshold: int = 2
    loop_repetitions: int = 2
    loop_cycle_lengths: tuple[int, ...] = (2, 3)
    excessive_tool_call_threshold: int = 6

    def __post_init__(self) -> None:
        thresholds = (
            self.repeated_failure_threshold,
            self.repeated_error_threshold,
            self.consecutive_failure_threshold,
            self.repeated_action_threshold,
            self.repeated_arguments_threshold,
            self.alternating_failure_threshold,
            self.timeout_cluster_threshold,
            self.loop_repetitions,
            self.excessive_tool_call_threshold,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in thresholds):
            raise DiagnosticValidationError("Pattern thresholds must be positive integers")
        if (not self.loop_cycle_lengths or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 2
                for value in self.loop_cycle_lengths)):
            raise DiagnosticValidationError("Loop cycle lengths must contain integers of at least 2")


class FailurePatternDetector:
    def __init__(self, config: PatternConfig | None = None) -> None:
        self.config = config or PatternConfig()

    def detect(self, events: Sequence[ExecutionEvent]) -> tuple[FailurePattern, ...]:
        prefix = tuple(events)
        _validate_events(prefix)
        selected = tuple(event for event in prefix if event.event_type == EventType.TOOL_SELECTED and event.tool_name)
        failures = tuple(event for event in prefix if event.event_type == EventType.TOOL_FAILED)
        patterns: list[FailurePattern] = []
        patterns.extend(self._repeated_tool_failures(failures))
        patterns.extend(self._repeated_errors(failures))
        patterns.extend(self._repeated_invalid_arguments(failures))
        consecutive = self._consecutive_failures(prefix)
        if consecutive is not None:
            patterns.append(consecutive)
        no_progress = self._retries_without_progress(prefix)
        if no_progress is not None:
            patterns.append(no_progress)
        alternating = self._alternating_failures(failures)
        if alternating is not None:
            patterns.append(alternating)
        patterns.extend(self._repeated_actions(selected))
        patterns.extend(self._repeated_arguments(selected))
        loop = self._possible_loop(selected)
        if loop is not None:
            patterns.append(loop)
        excessive = self._excessive_usage(selected)
        if excessive is not None:
            patterns.append(excessive)
        timeout_cluster = self._timeout_cluster(failures)
        if timeout_cluster is not None:
            patterns.append(timeout_cluster)
        consecutive_timeouts = self._consecutive_timeouts(prefix)
        if consecutive_timeouts is not None:
            patterns.append(consecutive_timeouts)
        limit_signal = self._execution_limit_signal(prefix)
        if limit_signal is not None:
            patterns.append(limit_signal)
        return tuple(patterns)

    def _repeated_tool_failures(self, failures: Sequence[ExecutionEvent]) -> list[FailurePattern]:
        grouped: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for event in failures:
            if event.tool_name:
                grouped[event.tool_name].append(event)
        result = []
        for tool_name, group in sorted(grouped.items()):
            if len(group) >= self.config.repeated_failure_threshold:
                result.append(FailurePattern(
                    pattern_type=PatternType.REPEATED_TOOL_FAILURE,
                    severity=PatternSeverity.HIGH if len(group) > self.config.repeated_failure_threshold else PatternSeverity.MEDIUM,
                    description=f"Tool {tool_name!r} failed {len(group)} times in the observed prefix",
                    event_ids=tuple(event.event_id for event in group),
                    tool_name=tool_name,
                    count=len(group),
                    threshold=self.config.repeated_failure_threshold,
                ))
        return result

    def _repeated_errors(self, failures: Sequence[ExecutionEvent]) -> list[FailurePattern]:
        grouped: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for event in failures:
            if event.error_type:
                grouped[event.error_type].append(event)
        result = []
        for error_type, group in sorted(grouped.items()):
            if len(group) >= self.config.repeated_error_threshold:
                result.append(FailurePattern(
                    pattern_type=PatternType.REPEATED_ERROR,
                    severity=PatternSeverity.MEDIUM,
                    description=f"Error type {error_type!r} repeated {len(group)} times in the observed prefix",
                    event_ids=tuple(event.event_id for event in group),
                    tool_name=_single_tool(group),
                    count=len(group),
                    threshold=self.config.repeated_error_threshold,
                    metadata={"error_type": error_type},
                ))
        return result

    def _repeated_invalid_arguments(self, failures: Sequence[ExecutionEvent]) -> list[FailurePattern]:
        invalid = [event for event in failures if _is_invalid_arguments(event) and event.tool_name]
        grouped: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for event in invalid:
            grouped[event.tool_name].append(event)
        result = []
        for tool_name, group in sorted(grouped.items()):
            if len(group) >= self.config.repeated_arguments_threshold:
                result.append(FailurePattern(
                    pattern_type=PatternType.REPEATED_INVALID_ARGUMENTS,
                    severity=PatternSeverity.HIGH,
                    description=f"Tool {tool_name!r} reported invalid arguments {len(group)} times",
                    event_ids=tuple(event.event_id for event in group),
                    tool_name=tool_name,
                    count=len(group),
                    threshold=self.config.repeated_arguments_threshold,
                ))
        return result

    def _consecutive_failures(self, prefix: Sequence[ExecutionEvent]) -> FailurePattern | None:
        outcomes = [event for event in prefix if event.event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED)]
        longest: list[ExecutionEvent] = []
        current: list[ExecutionEvent] = []
        for event in outcomes:
            if event.event_type == EventType.TOOL_FAILED:
                current.append(event)
                if len(current) > len(longest):
                    longest = list(current)
            else:
                current = []
        if len(longest) >= self.config.consecutive_failure_threshold:
            return FailurePattern(
                pattern_type=PatternType.CONSECUTIVE_FAILURES,
                severity=PatternSeverity.HIGH if len(longest) > self.config.consecutive_failure_threshold else PatternSeverity.MEDIUM,
                description=f"{len(longest)} consecutive tool failures were observed",
                event_ids=tuple(event.event_id for event in longest),
                tool_name=_single_tool(longest),
                count=len(longest),
                threshold=self.config.consecutive_failure_threshold,
            )
        return None

    def _retries_without_progress(self, prefix: Sequence[ExecutionEvent]) -> FailurePattern | None:
        outcomes = [event for event in prefix if event.event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED)]
        suffix: list[ExecutionEvent] = []
        for event in reversed(outcomes):
            if event.event_type != EventType.TOOL_FAILED:
                break
            suffix.append(event)
        suffix.reverse()
        threshold = self.config.consecutive_failure_threshold
        if len(suffix) < threshold:
            return None
        return FailurePattern(
            pattern_type=PatternType.RETRIES_WITHOUT_PROGRESS,
            severity=PatternSeverity.HIGH,
            description=f"{len(suffix)} consecutive failed tool outcomes occurred without a successful tool result",
            event_ids=tuple(event.event_id for event in suffix),
            tool_name=_single_tool(suffix),
            count=len(suffix),
            threshold=threshold,
        )

    def _repeated_actions(self, selected: Sequence[ExecutionEvent]) -> list[FailurePattern]:
        grouped: dict[str, list[ExecutionEvent]] = defaultdict(list)
        for event in selected:
            if event.tool_name:
                grouped[event.tool_name].append(event)
        result = []
        for tool_name, group in sorted(grouped.items()):
            if len(group) >= self.config.repeated_action_threshold:
                result.append(FailurePattern(
                    pattern_type=PatternType.REPEATED_ACTION,
                    severity=PatternSeverity.MEDIUM,
                    description=f"Tool {tool_name!r} was selected {len(group)} times in the observed prefix",
                    event_ids=tuple(event.event_id for event in group),
                    tool_name=tool_name,
                    count=len(group),
                    threshold=self.config.repeated_action_threshold,
                ))
        return result

    def _repeated_arguments(self, selected: Sequence[ExecutionEvent]) -> list[FailurePattern]:
        grouped: dict[tuple[str, str], list[ExecutionEvent]] = defaultdict(list)
        for event in selected:
            fingerprint = event.metadata.get("arguments_fingerprint")
            if event.tool_name and isinstance(fingerprint, str) and fingerprint:
                grouped[(event.tool_name, fingerprint)].append(event)
        result = []
        for (tool_name, _), group in sorted(grouped.items()):
            if len(group) >= self.config.repeated_arguments_threshold:
                result.append(FailurePattern(
                    pattern_type=PatternType.REPEATED_ARGUMENTS,
                    severity=PatternSeverity.HIGH if len(group) > self.config.repeated_arguments_threshold else PatternSeverity.MEDIUM,
                    description=f"Tool {tool_name!r} received the same argument fingerprint {len(group)} times",
                    event_ids=tuple(event.event_id for event in group),
                    tool_name=tool_name,
                    count=len(group),
                    threshold=self.config.repeated_arguments_threshold,
                ))
        return result

    def _alternating_failures(self, failures: Sequence[ExecutionEvent]) -> FailurePattern | None:
        required = self.config.alternating_failure_threshold
        suffix = failures[-required:]
        if len(suffix) < required or any(event.tool_name is None for event in suffix):
            return None
        names = [event.tool_name or "unknown" for event in suffix]
        if names[0] == names[1] or any(names[index] != names[index % 2] for index in range(len(names))):
            return None
        return FailurePattern(
            pattern_type=PatternType.ALTERNATING_FAILURES,
            severity=PatternSeverity.HIGH,
            description=f"The last {required} tool failures alternated between two tools",
            event_ids=tuple(event.event_id for event in suffix),
            count=required,
            threshold=required,
            metadata={"tools": list(dict.fromkeys(names))},
        )

    def _possible_loop(self, selected: Sequence[ExecutionEvent]) -> FailurePattern | None:
        tools = [event.tool_name for event in selected]
        for period in self.config.loop_cycle_lengths:
            required = period * self.config.loop_repetitions
            if len(tools) < required:
                continue
            suffix = tools[-required:]
            cycle = suffix[:period]
            if len(set(cycle)) < 2:
                continue
            if all(suffix[index] == cycle[index % period] for index in range(required)):
                repeated = selected[-required:]
                return FailurePattern(
                    pattern_type=PatternType.POSSIBLE_LOOP,
                    severity=PatternSeverity.HIGH,
                    description=(
                        f"The selected-tool sequence ended with {self.config.loop_repetitions} "
                        f"repetitions of a {period}-tool cycle"
                    ),
                    event_ids=tuple(event.event_id for event in repeated),
                    count=self.config.loop_repetitions,
                    threshold=self.config.loop_repetitions,
                    metadata={"cycle_length": period, "cycle_tools": list(cycle)},
                )
        return None

    def _excessive_usage(self, selected: Sequence[ExecutionEvent]) -> FailurePattern | None:
        if len(selected) < self.config.excessive_tool_call_threshold:
            return None
        return FailurePattern(
            pattern_type=PatternType.EXCESSIVE_TOOL_CALLS,
            severity=PatternSeverity.HIGH if len(selected) >= self.config.excessive_tool_call_threshold + 2 else PatternSeverity.MEDIUM,
            description=f"{len(selected)} tool calls were observed in the prefix",
            event_ids=tuple(event.event_id for event in selected),
            count=len(selected),
            threshold=self.config.excessive_tool_call_threshold,
        )

    def _timeout_cluster(self, failures: Sequence[ExecutionEvent]) -> FailurePattern | None:
        timeouts = [event for event in failures if _is_timeout(event)]
        if len(timeouts) < self.config.timeout_cluster_threshold:
            return None
        return FailurePattern(
            pattern_type=PatternType.TIMEOUT_CLUSTER,
            severity=PatternSeverity.MEDIUM,
            description=f"{len(timeouts)} timeout failures were observed",
            event_ids=tuple(event.event_id for event in timeouts),
            tool_name=_single_tool(timeouts),
            count=len(timeouts),
            threshold=self.config.timeout_cluster_threshold,
        )

    def _consecutive_timeouts(self, prefix: Sequence[ExecutionEvent]) -> FailurePattern | None:
        outcomes = [event for event in prefix if event.event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED)]
        suffix: list[ExecutionEvent] = []
        for event in reversed(outcomes):
            if event.event_type != EventType.TOOL_FAILED or not _is_timeout(event):
                break
            suffix.append(event)
        suffix.reverse()
        threshold = self.config.timeout_cluster_threshold
        if len(suffix) < threshold:
            return None
        return FailurePattern(
            pattern_type=PatternType.CONSECUTIVE_TIMEOUTS,
            severity=PatternSeverity.HIGH,
            description=f"The last {len(suffix)} tool outcomes were consecutive timeouts",
            event_ids=tuple(event.event_id for event in suffix),
            tool_name=_single_tool(suffix),
            count=len(suffix),
            threshold=threshold,
        )

    def _execution_limit_signal(self, prefix: Sequence[ExecutionEvent]) -> FailurePattern | None:
        matching = [event for event in prefix if _is_limit_event(event)]
        if not matching:
            return None
        return FailurePattern(
            pattern_type=PatternType.EXECUTION_LIMIT_SIGNAL,
            severity=PatternSeverity.HIGH,
            description="The execution reported that its tool-call limit was reached",
            event_ids=tuple(event.event_id for event in matching),
            tool_name=_single_tool(matching),
            count=len(matching),
            threshold=None,
        )


def _validate_events(events: Iterable[ExecutionEvent]) -> None:
    seen_ids: set[str] = set()
    identity: tuple[str, str] | None = None
    last_timestamp = None
    terminal_seen = False
    start_seen = False
    for index, event in enumerate(events):
        if event.event_id in seen_ids:
            raise DiagnosticValidationError("Diagnostic input contains duplicate event IDs")
        seen_ids.add(event.event_id)
        current = (event.execution_id, event.task_id)
        if identity is None:
            identity = current
        elif current != identity:
            raise DiagnosticValidationError("Diagnostic input mixes execution or task identifiers")
        if last_timestamp is not None and event.timestamp < last_timestamp:
            raise DiagnosticValidationError("Diagnostic input must be in timestamp order")
        last_timestamp = event.timestamp
        if terminal_seen:
            raise DiagnosticValidationError("Diagnostic input contains events after a terminal event")
        if event.event_type == EventType.EXECUTION_STARTED:
            if start_seen or index != 0:
                raise DiagnosticValidationError(
                    "A non-empty diagnostic prefix must contain exactly one execution_started event first"
                )
            start_seen = True
        elif not start_seen:
            raise DiagnosticValidationError(
                "A non-empty diagnostic prefix must begin with execution_started"
            )
        if event.event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED):
            expected_status = (
                "completed" if event.event_type == EventType.EXECUTION_COMPLETED else "failed"
            )
            if event.status != expected_status:
                raise DiagnosticValidationError(
                    f"{event.event_type.value} events must have status {expected_status!r}"
                )
            execution_status = event.metadata.get("execution_status")
            allowed_execution_statuses = (
                {"completed"} if event.event_type == EventType.EXECUTION_COMPLETED
                else {"failed", "partially_completed"}
            )
            if execution_status is not None and execution_status not in allowed_execution_statuses:
                raise DiagnosticValidationError(
                    f"{event.event_type.value} has an inconsistent execution_status metadata value"
                )
            terminal_seen = True


def _single_tool(events: Sequence[ExecutionEvent]) -> str | None:
    names = {event.tool_name for event in events if event.tool_name}
    return next(iter(names)) if len(names) == 1 else None


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


def _is_limit_event(event: ExecutionEvent) -> bool:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    return (
        "toolcalllimitexceeded" in text
        or "maximum tool call limit" in text
        or ("maximum of" in text and "tool call" in text)
    )
