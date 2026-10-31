"""Iterative LangGraph workflow for provider-backed tool selection and execution."""
from __future__ import annotations

import logging
import operator
import threading
import time
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from agentguard.config.settings import validate_failure_mode
from agentguard.schemas.events import EventCollector, EventType, ExecutionEvent
from agentguard.schemas.state import ExecutionState, ToolCall
from agentguard.tools.calculator import CalculatorArgs, calculate
from agentguard.tools.company import CompanyArgs, get_company
from agentguard.tools.search import SearchArgs, search

LOGGER = logging.getLogger("agentguard")
MAX_TOOL_CALLS = 8


class GraphState(TypedDict, total=False):
    messages: Annotated[list[Any], operator.add]
    execution_id: str
    task_id: str
    current_step: int


class _RunContext:
    """Mutable per-execution data shared with ToolNode tool wrappers."""

    def __init__(self, task: str, failure_mode: str | None, max_tool_calls: int) -> None:
        self.state = ExecutionState(user_input=task, execution_status="running", execution_mode="llm", failure_mode=failure_mode)
        self.events = EventCollector(self.state.execution_id, self.state.task_id)
        self.failure_mode = failure_mode
        self.max_tool_calls = max_tool_calls
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.limit_exceeded = False
        self.pending_tools: list[str] = []

    def emit(self, event_type: EventType, component: str, status: str, **kwargs: Any) -> ExecutionEvent:
        step_number = kwargs.pop("step_number", self.state.current_step)
        event = ExecutionEvent(execution_id=self.state.execution_id, task_id=self.state.task_id,
            step_number=step_number, event_type=event_type, component=component,
            status=status, **kwargs)
        self.events.emit(event)
        return event

    def invoke_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        # ToolNode can run sibling calls concurrently. Serialize execution bookkeeping
        # so each call has a clear, bounded step and ordered lifecycle events.
        with self.lock:
            if name in self.pending_tools:
                self.pending_tools.remove(name)
            self.state.current_step += 1
            call = ToolCall(tool_name=name, arguments=arguments.copy(), status="started")
            self.state.tool_calls.append(call)
            self.emit(EventType.TOOL_STARTED, "tool", "running", tool_name=name)
            started = time.monotonic()
            try:
                if len(self.state.tool_calls) > self.max_tool_calls:
                    self.limit_exceeded = True
                    raise RuntimeError("Maximum tool call limit reached")
                if self.failure_mode == "unavailable":
                    raise ConnectionError("Simulated tool unavailability")
                if self.failure_mode == "timeout":
                    raise TimeoutError("Simulated tool timeout")
                if self.failure_mode == "exception":
                    raise RuntimeError("Simulated tool execution exception")
                actual_arguments = arguments
                if self.failure_mode == "invalid_arguments":
                    actual_arguments = {"calculator": {"operation": "invalid", "a": "not-a-number", "b": 0},
                        "search": {"query": ""}, "company_data": {"name": ""}}[name]
                result = {"calculator": lambda: calculate(**actual_arguments),
                          "search": lambda: search(**actual_arguments),
                          "company_data": lambda: get_company(**actual_arguments)}[name]()
                call.status, call.result = "completed", result
                self.emit(EventType.TOOL_COMPLETED, "tool", "completed", tool_name=name,
                    duration_ms=(time.monotonic() - started) * 1000,
                    metadata={"result_type": type(result).__name__})
                return result
            except Exception as exc:
                call.status, call.error = "failed", str(exc)
                self.state.errors.append({"type": type(exc).__name__, "message": str(exc), "tool_name": name})
                self.emit(EventType.TOOL_FAILED, "tool", "failed", tool_name=name,
                    duration_ms=(time.monotonic() - started) * 1000,
                    error_type=type(exc).__name__, error_message=str(exc))
                return {"error": str(exc), "error_type": type(exc).__name__}

    def record_validation_failure(self, exc: Exception) -> str:
        with self.lock:
            message = "Tool input validation failed"
            name = self.pending_tools.pop(0) if self.pending_tools else "unknown"
            self.state.current_step += 1
            self.state.tool_calls.append(ToolCall(tool_name=name, arguments={}, status="failed", error=message))
            self.state.errors.append({"type": type(exc).__name__, "message": message, "tool_name": name})
            self.emit(EventType.TOOL_FAILED, "tool", "failed", tool_name=name,
                error_type=type(exc).__name__, error_message=message)
        return message


def _build_tools(context: _RunContext) -> list[Any]:
    from langchain_core.tools import StructuredTool

    specs = [("calculator", "Perform validated arithmetic.", CalculatorArgs),
             ("search", "Search AgentGuard's local mock dataset.", SearchArgs),
             ("company_data", "Retrieve a company from the local sample dataset.", CompanyArgs)]
    built = []
    for name, description, schema in specs:
        def invoke(_tool_name: str = name, **kwargs: Any) -> Any:
            return context.invoke_tool(_tool_name, kwargs)
        invoke.__name__ = name
        built.append(StructuredTool.from_function(func=invoke, name=name, description=description, args_schema=schema))
    return built


def run_llm(task: str, provider: str, model_name: str, *, failure_mode: str | None = None,
            max_tool_calls: int = MAX_TOOL_CALLS) -> tuple[ExecutionState, EventCollector]:
    """Run an iterative LLM -> ToolNode -> LLM graph with one stable execution context."""
    if max_tool_calls < 1:
        raise ValueError("max_tool_calls must be at least 1")
    failure_mode = validate_failure_mode(failure_mode)
    try:
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.graph import END, START, StateGraph
        from langgraph.prebuilt import ToolNode
    except ImportError as exc:
        raise RuntimeError("LLM mode requires LangGraph and a LangChain provider integration; install project dependencies.") from exc

    context = _RunContext(task, failure_mode, max_tool_calls)
    state, events = context.state, context.events
    context.emit(EventType.EXECUTION_STARTED, "agent", "running", metadata={"mode": "llm", "provider": provider, "model": model_name})
    tools = _build_tools(context)
    try:
        llm = init_chat_model(model_name, model_provider=provider).bind_tools(tools)
    except Exception as exc:
        _fail_context(context, exc, "LLM provider initialization failed")
        return state, events

    def llm_node(graph_state: GraphState) -> dict[str, Any]:
        request = context.emit(EventType.LLM_REQUEST, "llm", "started", metadata={"provider": provider, "model": model_name})
        started = time.monotonic()
        try:
            response = llm.invoke(graph_state["messages"])
            request.status = "completed"
            request.duration_ms = (time.monotonic() - started) * 1000
            requested_tools = getattr(response, "tool_calls", []) or []
            for index, tool_call in enumerate(requested_tools, start=1):
                context.pending_tools.append(str(tool_call.get("name", "unknown")))
                context.emit(EventType.TOOL_SELECTED, "agent", "selected", tool_name=tool_call.get("name"),
                    step_number=state.current_step + index,
                    metadata={"argument_names": sorted((tool_call.get("args") or {}).keys())})
            return {"messages": [response]}
        except Exception as exc:
            request.status = "failed"
            request.duration_ms = (time.monotonic() - started) * 1000
            request.error_type, request.error_message = type(exc).__name__, str(exc)
            raise

    def route_after_llm(graph_state: GraphState) -> str:
        response = graph_state["messages"][-1]
        calls = getattr(response, "tool_calls", []) or []
        if calls:
            if context.limit_exceeded or len(state.tool_calls) + len(calls) > max_tool_calls:
                context.limit_exceeded = True
                return "limit"
            return "tools"
        return "finalize"

    def limit_node(_: GraphState) -> dict[str, Any]:
        return {"messages": [AIMessage(content=f"Stopped after reaching the maximum of {max_tool_calls} tool calls.")]}

    tool_node = ToolNode(tools, handle_tool_errors=context.record_validation_failure)

    def execute_tools(graph_state: GraphState) -> dict[str, Any]:
        response = graph_state["messages"][-1]
        tool_messages = []
        for requested in getattr(response, "tool_calls", []) or []:
            # Invoke the supported ToolNode once per request to keep tool event and
            # state transitions sequential even if the model returned a call batch.
            single_call = AIMessage(content="", tool_calls=[requested])
            update = tool_node.invoke({"messages": [single_call]})
            tool_messages.extend(update.get("messages", []))
        return {"messages": tool_messages, "current_step": state.current_step}

    graph = StateGraph(GraphState)
    graph.add_node("llm", llm_node)
    graph.add_node("tools", execute_tools)
    graph.add_node("limit", limit_node)
    graph.add_node("finalize", lambda _: {})
    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", route_after_llm, {"tools": "tools", "finalize": "finalize", "limit": "limit"})
    graph.add_edge("tools", "llm")
    graph.add_edge("limit", "finalize")
    graph.add_edge("finalize", END)

    try:
        result = graph.compile().invoke({"messages": [HumanMessage(content=task)],
            "execution_id": state.execution_id, "task_id": state.task_id, "current_step": 0},
            config={"recursion_limit": (max_tool_calls * 2) + 8})
        if result.get("execution_id") != state.execution_id or result.get("task_id") != state.task_id:
            raise RuntimeError("LangGraph execution identifiers changed unexpectedly")
        final_message = result["messages"][-1]
        state.messages = [_message_dict(message) for message in result["messages"]]
        state.final_output = _content_text(getattr(final_message, "content", ""))
        if context.limit_exceeded or "maximum of" in state.final_output.lower():
            context.state.errors.append({"type": "ToolCallLimitExceeded", "message": state.final_output, "tool_name": ""})
        _finish_context(context)
    except Exception as exc:
        _fail_context(context, exc, "LangGraph execution failed")
        # Preserve whatever message trace LangGraph returned before the exception.
    return state, events


def _message_dict(message: Any) -> dict[str, Any]:
    role = "assistant" if getattr(message, "type", "") == "ai" else "tool" if getattr(message, "type", "") == "tool" else "user"
    return {"role": role, "name": getattr(message, "name", None), "content": _content_text(getattr(message, "content", "")),
            "tool_calls": getattr(message, "tool_calls", []) or []}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content or [])


def _finish_context(context: _RunContext) -> None:
    state = context.state
    successes = sum(call.status == "completed" for call in state.tool_calls)
    failures = sum(call.status == "failed" for call in state.tool_calls)
    if context.limit_exceeded or any(error.get("type") == "ToolCallLimitExceeded" for error in state.errors):
        state.execution_status = "partially_completed" if successes else "failed"
        terminal = EventType.EXECUTION_FAILED
        error = "Maximum tool call limit reached"
    elif failures or state.errors:
        state.execution_status = "partially_completed" if successes else "failed"
        terminal = EventType.EXECUTION_FAILED
        error = f"{failures} tool call(s) failed"
    else:
        state.execution_status, terminal, error = "completed", EventType.EXECUTION_COMPLETED, None
    state.end_time = datetime.now(timezone.utc)
    context.emit(terminal, "agent", state.execution_status,
        duration_ms=(time.monotonic() - context.started) * 1000,
        error_type="ExecutionIncomplete" if error else None, error_message=error,
        metadata={"successful_tool_calls": successes, "failed_tool_calls": failures})


def _fail_context(context: _RunContext, exc: Exception, message: str) -> None:
    LOGGER.exception(message, extra={"execution_id": context.state.execution_id})
    state = context.state
    state.errors.append({"type": type(exc).__name__, "message": str(exc), "tool_name": ""})
    successes = sum(call.status == "completed" for call in state.tool_calls)
    state.execution_status = "partially_completed" if successes else "failed"
    state.final_output = f"{message}: {exc}"
    state.end_time = datetime.now(timezone.utc)
    context.emit(EventType.EXECUTION_FAILED, "agent", state.execution_status,
        duration_ms=(time.monotonic() - context.started) * 1000,
        error_type=type(exc).__name__, error_message=str(exc))
