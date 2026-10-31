"""Deterministic features derived exclusively from the supplied event prefix."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Sequence

from agentguard.schemas.events import EventType, ExecutionEvent


FEATURE_COLUMNS = [
    "prefix_length", "elapsed_time_ms", "current_step_number",
    "total_tool_calls_so_far", "successful_tool_calls", "failed_tool_calls",
    "tool_failure_rate_so_far", "average_tool_duration_ms", "maximum_tool_duration_ms",
    "repeated_tool_call_count", "distinct_tool_count", "timeout_count",
    "invalid_argument_count", "tool_unavailable_count", "consecutive_failure_count",
    "time_since_last_failure_ms", "tool_switch_count", "repeated_failure_pattern_count",
]

# Explicit allow-list for model inputs. Dataset identifiers and eventual labels
# are deliberately not part of this feature matrix.
MODEL_FEATURE_COLUMNS = tuple(FEATURE_COLUMNS)

FEATURE_DICTIONARY = [
    {"name": "prefix_length", "type": "int", "definition": "Number of observed events in the prefix.", "source": "prefix", "inference_time": True},
    {"name": "elapsed_time_ms", "type": "float", "definition": "Last observed timestamp minus first observed timestamp, in milliseconds; null for fewer than two events.", "source": "timestamp", "inference_time": True},
    {"name": "current_step_number", "type": "int", "definition": "step_number on the final observed event.", "source": "step_number", "inference_time": True},
    {"name": "total_tool_calls_so_far", "type": "int", "definition": "Count of tool_selected events observed so far.", "source": "event_type", "inference_time": True},
    {"name": "successful_tool_calls", "type": "int", "definition": "Count of tool_completed events in this prefix.", "source": "event_type", "inference_time": True},
    {"name": "failed_tool_calls", "type": "int", "definition": "Count of tool_failed events in this prefix.", "source": "event_type", "inference_time": True},
    {"name": "tool_failure_rate_so_far", "type": "float", "definition": "failed / (failed + successful); null before any terminal tool outcome.", "source": "tool_failed, tool_completed", "inference_time": True},
    {"name": "average_tool_duration_ms", "type": "float", "definition": "Mean of observed non-null tool_completed/tool_failed durations.", "source": "duration_ms", "inference_time": True},
    {"name": "maximum_tool_duration_ms", "type": "float", "definition": "Maximum observed non-null tool duration.", "source": "duration_ms", "inference_time": True},
    {"name": "repeated_tool_call_count", "type": "int", "definition": "Observed selections minus distinct selected tools.", "source": "tool_selected.tool_name", "inference_time": True},
    {"name": "distinct_tool_count", "type": "int", "definition": "Number of distinct tools selected.", "source": "tool_selected.tool_name", "inference_time": True},
    {"name": "timeout_count", "type": "int", "definition": "Observed failures classified by TimeoutError or timeout wording.", "source": "tool_failed.error_type/error_message", "inference_time": True},
    {"name": "invalid_argument_count", "type": "int", "definition": "Observed failures classified by validation error or invalid-argument wording.", "source": "tool_failed.error_type/error_message", "inference_time": True},
    {"name": "tool_unavailable_count", "type": "int", "definition": "Observed failures classified by ConnectionError or unavailable wording.", "source": "tool_failed.error_type/error_message", "inference_time": True},
    {"name": "consecutive_failure_count", "type": "int", "definition": "Number of trailing failed tool outcomes in the observed prefix.", "source": "tool_failed/tool_completed sequence", "inference_time": True},
    {"name": "time_since_last_failure_ms", "type": "float", "definition": "Last prefix timestamp minus most recent observed tool_failed timestamp; null without a failure.", "source": "timestamp, tool_failed", "inference_time": True},
    {"name": "tool_switch_count", "type": "int", "definition": "Adjacent selected tool-name changes in the prefix.", "source": "tool_selected.tool_name", "inference_time": True},
    {"name": "repeated_failure_pattern_count", "type": "int", "definition": "Repeated observed error_type occurrences after the first occurrence.", "source": "tool_failed.error_type", "inference_time": True},
]


def extract_prefix_features(events: Sequence[ExecutionEvent]) -> dict[str, Any]:
    """Return features using no execution status or event outside ``events``."""
    if not events:
        return {column: (None if column in {"elapsed_time_ms", "tool_failure_rate_so_far", "average_tool_duration_ms", "maximum_tool_duration_ms", "time_since_last_failure_ms"} else 0) for column in FEATURE_COLUMNS}
    prefix = list(events)
    selected = [event for event in prefix if event.event_type == EventType.TOOL_SELECTED and event.tool_name]
    outcomes = [event for event in prefix if event.event_type in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED)]
    failed = [event for event in prefix if event.event_type == EventType.TOOL_FAILED]
    succeeded = sum(event.event_type == EventType.TOOL_COMPLETED for event in prefix)
    failure_rate = len(failed) / len(outcomes) if outcomes else None
    durations = [event.duration_ms for event in outcomes if event.duration_ms is not None]
    tools = [event.tool_name for event in selected]
    first_time, last_time = prefix[0].timestamp, prefix[-1].timestamp
    last_failure_age = (last_time - failed[-1].timestamp).total_seconds() * 1000 if failed else None
    trailing_failures = 0
    for outcome in reversed(outcomes):
        if outcome.event_type != EventType.TOOL_FAILED:
            break
        trailing_failures += 1
    failure_types = [event.error_type or "unknown" for event in failed]
    repeated_errors = len(failure_types) - len(set(failure_types))
    selected_switches = sum(left != right for left, right in zip(tools, tools[1:]))
    timeout_count = sum(_is_timeout(event) for event in failed)
    invalid_count = sum(_is_invalid_argument(event) for event in failed)
    unavailable_count = sum(_is_unavailable(event) for event in failed)
    return {
        "prefix_length": len(prefix),
        "elapsed_time_ms": (last_time - first_time).total_seconds() * 1000 if len(prefix) > 1 else None,
        "current_step_number": prefix[-1].step_number,
        "total_tool_calls_so_far": len(selected),
        "successful_tool_calls": succeeded,
        "failed_tool_calls": len(failed),
        "tool_failure_rate_so_far": failure_rate,
        "average_tool_duration_ms": sum(durations) / len(durations) if durations else None,
        "maximum_tool_duration_ms": max(durations) if durations else None,
        "repeated_tool_call_count": len(tools) - len(set(tools)),
        "distinct_tool_count": len(set(tools)),
        "timeout_count": timeout_count,
        "invalid_argument_count": invalid_count,
        "tool_unavailable_count": unavailable_count,
        "consecutive_failure_count": trailing_failures,
        "time_since_last_failure_ms": last_failure_age,
        "tool_switch_count": selected_switches,
        "repeated_failure_pattern_count": repeated_errors,
    }


def model_feature_matrix(frame: Any) -> Any:
    """Select only inference-time feature columns from a dataset DataFrame."""
    missing = set(MODEL_FEATURE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Dataset is missing model feature columns: {sorted(missing)}")
    return frame.loc[:, list(MODEL_FEATURE_COLUMNS)].copy()


def _is_timeout(event: ExecutionEvent) -> bool:
    return event.error_type == "TimeoutError" or "timeout" in (event.error_message or "").lower()


def _is_invalid_argument(event: ExecutionEvent) -> bool:
    text = f"{event.error_type or ''} {event.error_message or ''}".lower()
    return "validationerror" in text or "invalid argument" in text or "invalid_arguments" in text


def _is_unavailable(event: ExecutionEvent) -> bool:
    return event.error_type == "ConnectionError" or "unavailable" in (event.error_message or "").lower()
