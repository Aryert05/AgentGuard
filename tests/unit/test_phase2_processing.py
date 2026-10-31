from datetime import datetime, timedelta, timezone

import pytest

from agentguard.processing.features import FEATURE_COLUMNS, extract_prefix_features
from agentguard.processing.trace_processor import TraceValidationError, group_execution_traces, normalize_trace
from agentguard.schemas.events import EventType, ExecutionEvent


EXECUTION_ID = "203d9df8-2b3f-4f8e-b87e-81eb7e1b9c1c"
TASK_ID = "b819631c-fb66-47c5-91ad-28cda3b6b970"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _event(index, kind, *, tool=None, status="ok", duration=None, error_type=None, error=None, execution=EXECUTION_ID):
    return ExecutionEvent(event_id=f"event-{index}", execution_id=execution, task_id=TASK_ID,
        step_number=index, timestamp=BASE + timedelta(milliseconds=index * 10), event_type=kind,
        component="tool" if tool else "agent", tool_name=tool, status=status,
        duration_ms=duration, error_type=error_type, error_message=error)


def test_trace_processor_sorts_deduplicates_and_marks_incomplete():
    execution = {"execution_id": EXECUTION_ID, "task_id": TASK_ID, "execution_status": "running"}
    start = _event(0, EventType.EXECUTION_STARTED)
    selected = _event(1, EventType.TOOL_SELECTED, tool="calculator")
    trace = normalize_trace(execution, [selected, start, selected])
    assert trace.events_reordered and trace.duplicate_events_removed == 1
    assert not trace.complete
    assert [event.event_id for event in trace.events] == ["event-0", "event-1"]


def test_trace_processor_rejects_identity_and_conflicting_duplicates():
    execution = {"execution_id": EXECUTION_ID, "task_id": TASK_ID}
    with pytest.raises(TraceValidationError, match="identity"):
        normalize_trace(execution, [_event(1, EventType.TOOL_FAILED, execution="wrong")])
    one = _event(1, EventType.TOOL_FAILED, tool="calculator", error_type="ValueError")
    conflicting = one.model_copy(update={"status": "different"})
    with pytest.raises(TraceValidationError, match="Conflicting"):
        normalize_trace(execution, [one, conflicting])


def test_raw_trace_mappings_require_explicit_event_id_and_timestamp_but_live_events_keep_defaults():
    payload = _event(1, EventType.TOOL_SELECTED, tool="calculator").model_dump(mode="json")
    missing_id = dict(payload)
    missing_id.pop("event_id")
    with pytest.raises(TraceValidationError, match="event_id"):
        normalize_trace({"execution_id": EXECUTION_ID, "task_id": TASK_ID}, [missing_id])
    missing_timestamp = dict(payload)
    missing_timestamp.pop("timestamp")
    with pytest.raises(TraceValidationError, match="timestamp"):
        normalize_trace({"execution_id": EXECUTION_ID, "task_id": TASK_ID}, [missing_timestamp])

    live = ExecutionEvent(execution_id=EXECUTION_ID, task_id=TASK_ID,
        event_type=EventType.EXECUTION_STARTED, component="agent", status="running")
    assert live.event_id and live.timestamp.tzinfo is not None


def test_raw_trace_mapping_parses_and_normalizes_non_utc_timestamp():
    payload = _event(1, EventType.TOOL_SELECTED, tool="calculator").model_dump(mode="json")
    payload["timestamp"] = "2026-01-01T05:30:00+05:30"
    trace = normalize_trace({"execution_id": EXECUTION_ID, "task_id": TASK_ID}, [payload])
    assert trace.events[0].timestamp == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_trace_processor_marks_complete_terminal_order_and_rejects_multiple_terminals():
    execution = {"execution_id": EXECUTION_ID, "task_id": TASK_ID}
    complete = normalize_trace(execution, [_event(0, EventType.EXECUTION_STARTED), _event(2, EventType.EXECUTION_COMPLETED)])
    assert complete.complete
    with pytest.raises(TraceValidationError, match="multiple start or terminal"):
        normalize_trace(execution, [_event(0, EventType.EXECUTION_STARTED), _event(1, EventType.EXECUTION_FAILED), _event(2, EventType.EXECUTION_COMPLETED)])


def test_group_execution_traces_separates_events_and_rejects_orphans():
    execution = {"execution_id": EXECUTION_ID, "task_id": TASK_ID}
    traces = group_execution_traces([execution], [_event(0, EventType.EXECUTION_STARTED)])
    assert list(traces) == [EXECUTION_ID]
    assert len(traces[EXECUTION_ID].events) == 1
    with pytest.raises(TraceValidationError, match="unknown execution_id"):
        group_execution_traces([execution], [_event(1, EventType.TOOL_FAILED, execution="unknown")])


def test_empty_and_single_event_prefixes_are_explicit():
    empty = extract_prefix_features([])
    assert empty["prefix_length"] == 0 and empty["elapsed_time_ms"] is None
    assert empty["tool_failure_rate_so_far"] is None
    assert empty["average_tool_duration_ms"] is None
    assert empty["maximum_tool_duration_ms"] is None
    assert empty["time_since_last_failure_ms"] is None
    assert empty["failed_tool_calls"] == 0 and empty["distinct_tool_count"] == 0
    single = extract_prefix_features([_event(0, EventType.EXECUTION_STARTED)])
    assert single["prefix_length"] == 1 and single["elapsed_time_ms"] is None
    assert single["tool_failure_rate_so_far"] is None
    assert set(single) == set(FEATURE_COLUMNS)


def test_prefix_features_count_tools_failures_switches_and_durations():
    events = [
        _event(0, EventType.EXECUTION_STARTED),
        _event(1, EventType.TOOL_SELECTED, tool="calculator"),
        _event(2, EventType.TOOL_COMPLETED, tool="calculator", duration=10),
        _event(3, EventType.TOOL_SELECTED, tool="company_data"),
        _event(4, EventType.TOOL_FAILED, tool="company_data", duration=30, error_type="TimeoutError", error="simulated timeout"),
        _event(5, EventType.TOOL_SELECTED, tool="company_data"),
        _event(6, EventType.TOOL_FAILED, tool="company_data", duration=50, error_type="TimeoutError", error="simulated timeout"),
    ]
    features = extract_prefix_features(events)
    assert features["total_tool_calls_so_far"] == 3
    assert features["successful_tool_calls"] == 1 and features["failed_tool_calls"] == 2
    assert features["tool_failure_rate_so_far"] == 2 / 3
    assert features["average_tool_duration_ms"] == 30
    assert features["maximum_tool_duration_ms"] == 50
    assert features["distinct_tool_count"] == 2 and features["repeated_tool_call_count"] == 1
    assert features["tool_switch_count"] == 1
    assert features["timeout_count"] == 2
    assert features["consecutive_failure_count"] == 2
    assert features["repeated_failure_pattern_count"] == 1
    assert features["time_since_last_failure_ms"] == 0


def test_prefix_features_cannot_observe_later_events():
    observed = [_event(0, EventType.EXECUTION_STARTED), _event(1, EventType.TOOL_SELECTED, tool="calculator")]
    future = _event(2, EventType.TOOL_FAILED, tool="calculator", error_type="TimeoutError", error="timeout")
    features = extract_prefix_features(observed)
    assert features["failed_tool_calls"] == 0
    assert features["timeout_count"] == 0
    assert features["prefix_length"] == len(observed)
    assert future not in observed
