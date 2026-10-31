"""Validate and normalize one execution trace independently of storage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any, Mapping, Sequence

from agentguard.schemas.events import EventType, ExecutionEvent


class TraceValidationError(ValueError):
    """Raised when trace records cannot form one internally consistent trace."""


@dataclass(frozen=True)
class NormalizedTrace:
    execution: dict[str, Any]
    events: tuple[ExecutionEvent, ...]
    complete: bool
    duplicate_events_removed: int
    events_reordered: bool

    @property
    def execution_id(self) -> str:
        return str(self.execution["execution_id"])

    @property
    def task_id(self) -> str:
        return str(self.execution["task_id"])


def normalize_trace(execution: Mapping[str, Any], raw_events: Sequence[ExecutionEvent | Mapping[str, Any]]) -> NormalizedTrace:
    required = ("execution_id", "task_id")
    if any(not execution.get(key) for key in required):
        raise TraceValidationError("Execution record must include execution_id and task_id")
    execution_id, task_id = str(execution["execution_id"]), str(execution["task_id"])
    unique: dict[str, ExecutionEvent] = {}
    duplicates = 0
    for raw in raw_events:
        if isinstance(raw, Mapping):
            missing = [field for field in ("event_id", "timestamp") if field not in raw]
            if missing:
                raise TraceValidationError(
                    "Raw event record must explicitly include " + " and ".join(missing)
                )
        try:
            event = raw if isinstance(raw, ExecutionEvent) else ExecutionEvent.model_validate(raw)
        except Exception as exc:
            raise TraceValidationError(f"Malformed event record: {exc}") from exc
        if event.execution_id != execution_id or event.task_id != task_id:
            raise TraceValidationError("Execution and event identity values do not match")
        event = event.model_copy(update={"timestamp": event.timestamp.astimezone(timezone.utc)})
        previous = unique.get(event.event_id)
        if previous is not None:
            if previous != event:
                raise TraceValidationError(f"Conflicting records share event_id {event.event_id}")
            duplicates += 1
            continue
        unique[event.event_id] = event
    original = list(unique.values())
    ordered = sorted(original, key=lambda item: item.timestamp)
    reordered = [item.event_id for item in ordered] != [item.event_id for item in original]
    starts = [index for index, event in enumerate(ordered) if event.event_type == EventType.EXECUTION_STARTED]
    terminals = [index for index, event in enumerate(ordered)
                 if event.event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED)]
    if len(starts) > 1 or len(terminals) > 1:
        raise TraceValidationError("Trace contains multiple start or terminal events")
    complete = bool(starts == [0] and terminals == [len(ordered)-1] and ordered)
    return NormalizedTrace(execution=dict(execution), events=tuple(ordered), complete=complete,
        duplicate_events_removed=duplicates, events_reordered=reordered)


def group_execution_traces(executions: Sequence[Mapping[str, Any]],
                           events: Sequence[ExecutionEvent | Mapping[str, Any]]) -> dict[str, NormalizedTrace]:
    """Group event records by execution ID and normalize each known execution."""
    execution_by_id = {str(record.get("execution_id")): record for record in executions}
    if "None" in execution_by_id or len(execution_by_id) != len(executions):
        raise TraceValidationError("Execution records must have unique execution_id values")
    grouped: dict[str, list[ExecutionEvent | Mapping[str, Any]]] = {key: [] for key in execution_by_id}
    for event in events:
        raw_id = event.execution_id if isinstance(event, ExecutionEvent) else event.get("execution_id")
        execution_id = str(raw_id)
        if execution_id not in grouped:
            raise TraceValidationError(f"Event refers to unknown execution_id {execution_id}")
        grouped[execution_id].append(event)
    return {execution_id: normalize_trace(record, grouped[execution_id])
            for execution_id, record in execution_by_id.items()}
