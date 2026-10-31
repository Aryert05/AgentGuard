"""Outcome labels use only the final execution state; prefix features do not."""
from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from agentguard.schemas.events import EventType, ExecutionEvent


def execution_labels(execution_status: str, events: Sequence[ExecutionEvent]) -> dict[str, Any]:
    """Return eventual labels. Partial completion counts as eventual failure (target=1)."""
    if execution_status == "completed":
        return {"eventual_failure": 0, "outcome_label": "completed", "failure_category": "none"}
    if execution_status not in {"failed", "partially_completed"}:
        return {"eventual_failure": None, "outcome_label": "unknown", "failure_category": None}
    failures = [event for event in events if event.event_type == EventType.TOOL_FAILED]
    categories = [_failure_category(event.error_type, event.error_message) for event in failures]
    category = Counter(categories).most_common(1)[0][0] if categories else "execution_failure"
    if execution_status == "partially_completed":
        category = "partial_execution" if category == "execution_failure" else f"partial_{category}"
    return {"eventual_failure": 1, "outcome_label": execution_status, "failure_category": category}


def _failure_category(error_type: str | None, message: str | None) -> str:
    lower = (message or "").lower()
    if error_type == "TimeoutError" or "timeout" in lower:
        return "timeout"
    if error_type == "ConnectionError" or "unavailable" in lower:
        return "tool_unavailable"
    if error_type == "ValidationError" or "invalid argument" in lower:
        return "invalid_arguments"
    if "simulated tool execution exception" in lower:
        return "tool_exception"
    return "tool_error"
