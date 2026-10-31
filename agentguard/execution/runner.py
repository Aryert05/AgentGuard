"""Shared execution engine and structured event lifecycle."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid4, uuid5

from agentguard.schemas.events import EventCollector, EventType, ExecutionEvent
from agentguard.schemas.state import ExecutionState, ToolCall
from agentguard.config.settings import validate_failure_mode
from agentguard.tools.calculator import calculate
from agentguard.tools.company import get_company
from agentguard.tools.search import search

LOGGER = logging.getLogger("agentguard")
TOOLS = {"calculator": calculate, "search": search, "company_data": get_company}


class DeterministicClock:
    """Synthetic clock for reproducible generated traces; never used by live runs."""

    def __init__(self, origin: datetime, *, step_seconds: float = 0.01) -> None:
        if origin.tzinfo is None or origin.utcoffset() is None:
            raise ValueError("Deterministic clock origin must be timezone-aware")
        self.origin, self.step_seconds, self.tick = origin.astimezone(timezone.utc), step_seconds, 0

    def now(self) -> datetime:
        value = self.origin + timedelta(seconds=self.tick * self.step_seconds)
        self.tick += 1
        return value

    def monotonic(self) -> float:
        value = self.tick * self.step_seconds
        self.tick += 1
        return value


def _emit(state: ExecutionState, collector: EventCollector, kind: EventType, component: str,
          status: str, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
          deterministic_event_ids: bool = False, **kwargs: Any) -> None:
    event_kwargs = {"event_id": str(uuid5(UUID(state.execution_id), f"event-{len(collector.events)}"))} if deterministic_event_ids else {}
    collector.emit(ExecutionEvent(**event_kwargs, execution_id=state.execution_id, task_id=state.task_id, timestamp=now(),
        step_number=state.current_step, event_type=kind, component=component, status=status,
        metadata=_safe_metadata(kwargs.pop("metadata", {})), **kwargs))


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep event metadata useful without copying tool payloads or credential-like fields."""
    sensitive = ("api_key", "access_token", "authorization", "credential", "password", "secret", "token")
    return {key: "[REDACTED]" if any(term in key.lower() for term in sensitive) else value
            for key, value in metadata.items() if key not in {"arguments", "result"}}


def _plan(task: str) -> list[tuple[str, dict[str, Any]]]:
    """Small deterministic planner for offline demo; explicit about rule-based routing."""
    text = task.strip()
    low = text.lower()
    calls: list[tuple[str, dict[str, Any]]] = []
    # Cost / arithmetic requests can be decomposed into multiplication then addition.
    match = re.search(r"(\d+(?:\.\d+)?)\s+(?:products?|items?)\s+(?:priced|costing|at)\s+(?:\$|₹)?(\d+(?:\.\d+)?)", low)
    if match:
        calls.append(("calculator", {"operation": "multiply", "a": float(match.group(1)), "b": float(match.group(2))}))
    elif re.search(r"\b(calculate|add|subtract|multiply|divide|total|sum|plus|minus|times)\b", low):
        nums = re.findall(r"-?\d+(?:\.\d+)?", low)
        if len(nums) < 2:
            raise ValueError("Deterministic planner needs two numbers for arithmetic")
        op = "multiply" if re.search(r"\b(multiply|each|times|product)\b", low) else "subtract" if re.search(r"\b(subtract|minus)\b", low) else "divide" if re.search(r"\bdivide\b", low) else "add"
        calls.append(("calculator", {"operation": op, "a": float(nums[0]), "b": float(nums[1])}))
    if any(w in low for w in ("company", "tata motors", "infosys", "wipro")):
        name = next((n for n in ("Tata Motors", "Infosys", "Wipro") if n.lower() in low), text)
        calls.append(("company_data", {"name": name}))
    if any(w in low for w in ("search", "find", "look up", "information about")) and not calls:
        calls.append(("search", {"query": text}))
    if not calls:
        calls.append(("search", {"query": text}))
    return calls


def run_plan(task: str, *, failure_mode: str | None = None,
             plan: list[tuple[str, dict[str, Any]]] | None = None,
             execution_id: Optional[str] = None, task_id: Optional[str] = None,
             clock: Optional[DeterministicClock] = None,
             deterministic_event_ids: bool = False,
             log_failures: bool = True) -> tuple[ExecutionState, EventCollector]:
    failure_mode = validate_failure_mode(failure_mode)
    now = clock.now if clock else lambda: datetime.now(timezone.utc)
    timer = clock.monotonic if clock else time.monotonic
    state = ExecutionState(user_input=task, execution_id=execution_id or str(uuid4()),
        task_id=task_id or str(uuid4()), start_time=now(),
        failure_mode=failure_mode, execution_status="running")
    events = EventCollector(state.execution_id, state.task_id)
    started = timer()
    _emit(state, events, EventType.EXECUTION_STARTED, "agent", "running", now=now,
          deterministic_event_ids=deterministic_event_ids, metadata={"mode": "deterministic"})
    outputs: list[str] = []
    try:
        for tool_name, arguments in (plan if plan is not None else _plan(task)):
            state.current_step += 1
            _emit(state, events, EventType.TOOL_SELECTED, "agent", "selected", now=now,
                  deterministic_event_ids=deterministic_event_ids, tool_name=tool_name,
                  metadata={"argument_names": sorted(arguments)})
            call = ToolCall(tool_name=tool_name, arguments=arguments, status="started")
            state.tool_calls.append(call)
            _emit(state, events, EventType.TOOL_STARTED, "tool", "running", now=now,
                  deterministic_event_ids=deterministic_event_ids, tool_name=tool_name)
            t0 = timer()
            try:
                if tool_name not in TOOLS:
                    raise LookupError(f"Unknown tool: {tool_name}")
                if failure_mode == "unavailable":
                    raise ConnectionError("Simulated tool unavailability")
                if failure_mode == "timeout":
                    raise TimeoutError("Simulated tool timeout")
                if failure_mode == "exception":
                    raise RuntimeError("Simulated tool execution exception")
                if failure_mode == "invalid_arguments":
                    arguments = {"operation": "invalid", "a": "not-a-number", "b": 0}
                result = TOOLS[tool_name](**arguments)
                call.status, call.result = "completed", result
                outputs.append(str(result))
                _emit(state, events, EventType.TOOL_COMPLETED, "tool", "completed", now=now,
                      deterministic_event_ids=deterministic_event_ids, tool_name=tool_name,
                      duration_ms=(timer()-t0)*1000, metadata={"result_type": type(result).__name__})
            except Exception as exc:  # A failed tool is an execution event, not a process crash.
                call.status, call.error = "failed", str(exc)
                error = {"type": type(exc).__name__, "message": str(exc), "tool_name": tool_name}
                state.errors.append(error)
                if log_failures:
                    LOGGER.exception("Tool execution failed", extra={"execution_id": state.execution_id, "tool_name": tool_name})
                _emit(state, events, EventType.TOOL_FAILED, "tool", "failed", now=now,
                      deterministic_event_ids=deterministic_event_ids, tool_name=tool_name,
                      duration_ms=(timer()-t0)*1000, error_type=type(exc).__name__, error_message=str(exc))
            state.messages.append({"role": "tool", "name": tool_name, "content": call.result if call.status == "completed" else call.error})
        state.final_output = "\n".join(outputs) if outputs else "No tool result was produced."
        state.execution_status = ("failed" if not outputs else "partially_completed") if state.errors else "completed"
        terminal_type = EventType.EXECUTION_COMPLETED if state.execution_status == "completed" else EventType.EXECUTION_FAILED
        _emit(state, events, terminal_type, "agent", state.execution_status, now=now,
              deterministic_event_ids=deterministic_event_ids, duration_ms=(timer()-started)*1000,
              error_type="ToolExecutionError" if state.errors else None,
              error_message=f"{len(state.errors)} tool call(s) failed" if state.errors else None,
              metadata={"successful_tool_calls": len(outputs), "failed_tool_calls": len(state.errors)})
    except Exception as exc:
        state.execution_status, state.final_output = "failed", f"Execution failed: {exc}"
        state.errors.append({"type": type(exc).__name__, "message": str(exc), "tool_name": ""})
        _emit(state, events, EventType.EXECUTION_FAILED, "agent", "failed", now=now,
              deterministic_event_ids=deterministic_event_ids,
              duration_ms=(timer()-started)*1000, error_type=type(exc).__name__, error_message=str(exc))
    state.end_time = now()
    return state, events
