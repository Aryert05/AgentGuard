import json
import time
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agentguard.agent.runtime import run_agent
from agentguard.config.settings import ConfigurationError, Settings
from agentguard.execution.runner import run_plan
from agentguard.schemas.events import EventCollector, EventType, ExecutionEvent
from agentguard.tools.calculator import calculate
from agentguard.tools.company import get_company
from agentguard.tools.search import search


def _install_fake_model(monkeypatch, responses, on_invoke=None):
    import langchain.chat_models

    class FakeModel:
        def bind_tools(self, tools):
            assert {tool.name for tool in tools} == {"calculator", "search", "company_data"}
            return self

        def invoke(self, messages):
            if on_invoke:
                on_invoke(messages)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", lambda *args, **kwargs: FakeModel())


def _call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _events_for(state, events):
    assert events.events
    assert {event.execution_id for event in events.events} == {state.execution_id}
    assert {event.task_id for event in events.events} == {state.task_id}
    assert events.events[0].event_type == EventType.EXECUTION_STARTED
    assert events.events[-1].event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED)
    assert state.end_time is not None and state.end_time.tzinfo is not None


def test_initialization_and_unique_ids():
    a, _ = run_agent("calculate 2 plus 3", demo=True)
    b, _ = run_agent("calculate 2 plus 3", demo=True)
    assert a.execution_id != b.execution_id and a.task_id != b.task_id


@pytest.mark.parametrize(("operation", "a", "b", "expected"), [
    ("add", 9, 3, 12), ("subtract", 9, 3, 6), ("multiply", 9, 3, 27), ("divide", 9, 3, 3),
])
def test_calculator_operations(operation, a, b, expected):
    assert calculate(operation, a, b)["result"] == expected


def test_calculator_division_by_zero_and_bad_arguments():
    with pytest.raises(ZeroDivisionError):
        calculate("divide", 1, 0)
    with pytest.raises(Exception):
        calculate("invalid", 1, 2)


def test_calculator_demo_and_company_demo():
    state, events = run_agent("Calculate the total cost of 15 products priced at 250 each", demo=True)
    assert state.tool_calls[0].result["result"] == 3750
    _events_for(state, events)
    company_state, _ = run_agent("Find information about Tata Motors and summarize the available company data", demo=True)
    assert company_state.tool_calls[0].result["name"] == "Tata Motors"


def test_search_relevant_and_no_match():
    result = search("Search for tool calling")
    assert result["source_type"] == "local_mock_dataset"
    assert result["matched"] and result["results"][0]["title"] == "Tool calling"
    missing = search("unobtainium orbital zeppelin")
    assert missing["matched"] is False and missing["results"] == []
    assert missing["source_type"] == "local_mock_dataset"


def test_unknown_company_is_explicit():
    with pytest.raises(LookupError, match="Unknown company"):
        get_company("No Such Company")


def test_deterministic_multiple_tools_and_partial_success():
    state, events = run_plan("two tools", plan=[
        ("calculator", {"operation": "multiply", "a": 4, "b": 5}),
        ("company_data", {"name": "No Such Company"}),
    ])
    assert state.execution_status == "partially_completed"
    assert [call.status for call in state.tool_calls] == ["completed", "failed"]
    assert events.events[-1].event_type == EventType.EXECUTION_FAILED
    _events_for(state, events)


@pytest.mark.parametrize("mode", ["invalid_arguments", "timeout", "unavailable", "exception"])
def test_failure_injection_modes_record_failures(mode):
    state, events = run_plan("task", plan=[("calculator", {"operation": "add", "a": 1, "b": 2})], failure_mode=mode)
    assert state.execution_status == "failed"
    assert state.tool_calls[0].status == "failed"
    assert any(event.event_type == EventType.TOOL_FAILED for event in events.events)


def test_failure_mode_configuration_is_validated():
    with pytest.raises(ConfigurationError, match="AGENTGUARD_FAILURE_MODE"):
        Settings(None, None, "silently-ignore")
    with pytest.raises(ConfigurationError):
        run_plan("task", failure_mode="silently-ignore")


def test_event_json_and_collector_invariants():
    state, events = run_plan("calculate", plan=[("calculator", {"operation": "add", "a": 1, "b": 2})])
    decoded = json.loads(events.to_json())
    assert decoded[0]["event_type"] == "execution_started"
    assert decoded[-1]["event_type"] == "execution_completed"
    assert len({row["event_id"] for row in decoded}) == len(decoded)
    assert all(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).utcoffset() is not None for row in decoded)
    with pytest.raises(ValueError, match="timezone-aware"):
        ExecutionEvent(execution_id=state.execution_id, task_id=state.task_id, timestamp=datetime.now(), event_type=EventType.TOOL_STARTED, component="tool", status="running")
    collector = EventCollector(state.execution_id, state.task_id)
    collector.emit(ExecutionEvent(execution_id=state.execution_id, task_id=state.task_id, event_type=EventType.EXECUTION_STARTED, component="agent", status="running"))
    with pytest.raises(ValueError, match="do not match"):
        collector.emit(ExecutionEvent(execution_id="wrong", task_id=state.task_id, event_type=EventType.TOOL_STARTED, component="tool", status="running"))
    with pytest.raises(ValueError, match="timestamp order"):
        collector.emit(ExecutionEvent(execution_id=state.execution_id, task_id=state.task_id, timestamp=datetime(2000, 1, 1, tzinfo=timezone.utc), event_type=EventType.TOOL_STARTED, component="tool", status="running"))
    safe = ExecutionEvent(execution_id=state.execution_id, task_id=state.task_id,
        event_type=EventType.TOOL_COMPLETED, component="tool", status="completed",
        metadata={"api_key": "should-not-appear", "label": "access_token=very-secret", "token_text": "sk-proj-012345678901234567890123"})
    assert safe.metadata == {"api_key": "[REDACTED]", "label": "access_token=[REDACTED]", "token_text": "[REDACTED]"}


def test_missing_llm_configuration_and_deterministic_demo():
    with pytest.raises(RuntimeError, match="--demo"):
        run_agent("calculate 1 plus 1", settings=Settings(None, None, None))
    state, _ = run_agent("calculate 1 plus 1", demo=True)
    assert state.execution_status == "completed"


def test_langgraph_iterative_tool_llm_tool_llm_final(monkeypatch):
    seen_messages = []

    def inspect(messages):
        seen_messages.append(messages)
        if len(seen_messages) == 2:
            assert any(isinstance(message, ToolMessage) and "42" in str(message.content) for message in messages)
        if len(seen_messages) == 3:
            assert any(isinstance(message, ToolMessage) and "Infosys" in str(message.content) for message in messages)

    _install_fake_model(monkeypatch, [
        _call("calculator", {"operation": "multiply", "a": 6, "b": 7}, "c1"),
        _call("company_data", {"name": "Infosys"}, "c2"),
        AIMessage(content="The result is 42. Infosys is an IT company headquartered in Bengaluru.")], inspect)
    from agentguard.agent.graph import run_llm
    state, events = run_llm("Calculate 6 times 7, then look up Infosys", "openai", "fake-model")
    assert len(seen_messages) == 3
    assert [call.tool_name for call in state.tool_calls] == ["calculator", "company_data"]
    assert state.final_output.startswith("The result is 42")
    assert state.execution_status == "completed"
    assert [event.event_type for event in events.events].count(EventType.LLM_REQUEST) == 3
    _events_for(state, events)


def test_langgraph_executes_batched_tool_calls_sequentially(monkeypatch):
    _install_fake_model(monkeypatch, [
        AIMessage(content="", tool_calls=[
            {"name": "calculator", "args": {"operation": "add", "a": 2, "b": 3}, "id": "c1", "type": "tool_call"},
            {"name": "company_data", "args": {"name": "Wipro"}, "id": "c2", "type": "tool_call"},
        ]),
        AIMessage(content="The sum is five; Wipro is an IT company headquartered in Bengaluru.")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("calculate and look up Wipro", "openai", "fake-model")
    assert [call.tool_name for call in state.tool_calls] == ["calculator", "company_data"]
    assert [call.status for call in state.tool_calls] == ["completed", "completed"]
    _events_for(state, events)


def test_provider_execution_has_consistent_ids_and_total_duration(monkeypatch):
    def delayed(messages):
        time.sleep(0.015)
    _install_fake_model(monkeypatch, [_call("calculator", {"operation": "add", "a": 2, "b": 3}, "c1"), AIMessage(content="The answer is five.")], delayed)
    from agentguard.agent.graph import run_llm
    state, events = run_llm("calculate", "openai", "fake-model")
    duration = (state.end_time - state.start_time).total_seconds() * 1000
    assert duration >= 20
    assert state.final_output == "The answer is five."
    _events_for(state, events)


def test_tool_call_limit_stops_graph(monkeypatch):
    _install_fake_model(monkeypatch, [
        _call("calculator", {"operation": "add", "a": 1, "b": 1}, "c1"),
        _call("company_data", {"name": "Infosys"}, "c2")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("two things", "openai", "fake-model", max_tool_calls=1)
    assert state.execution_status == "partially_completed"
    assert len(state.tool_calls) == 1
    assert "maximum" in state.final_output.lower()
    _events_for(state, events)


def test_provider_tool_failure_and_partial_success(monkeypatch):
    _install_fake_model(monkeypatch, [
        _call("calculator", {"operation": "add", "a": 1, "b": 1}, "c1"),
        AIMessage(content="A tool failed but the earlier result is available.")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("task", "openai", "fake-model", failure_mode="exception")
    assert state.execution_status == "failed"
    assert any(event.event_type == EventType.TOOL_FAILED for event in events.events)
    _events_for(state, events)


def test_provider_partial_success_status(monkeypatch):
    _install_fake_model(monkeypatch, [
        _call("calculator", {"operation": "add", "a": 1, "b": 1}, "c1"),
        _call("company_data", {"name": "No Such Company"}, "c2"),
        AIMessage(content="I calculated the value, but the company was not found.")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("task", "openai", "fake-model")
    assert state.execution_status == "partially_completed"
    assert [call.status for call in state.tool_calls] == ["completed", "failed"]
    _events_for(state, events)


def test_provider_tool_schema_failure_is_recorded(monkeypatch):
    _install_fake_model(monkeypatch, [
        _call("calculator", {"operation": "add", "a": "invalid", "b": 3}, "c1"),
        AIMessage(content="The calculator arguments were invalid.")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("calculate", "openai", "fake-model")
    assert state.execution_status == "failed"
    assert state.tool_calls[0].tool_name == "calculator"
    assert any(event.event_type == EventType.TOOL_FAILED for event in events.events)
    _events_for(state, events)


def test_provider_error_has_complete_lifecycle(monkeypatch):
    _install_fake_model(monkeypatch, [ConnectionError("provider unavailable")])
    from agentguard.agent.graph import run_llm
    state, events = run_llm("task", "openai", "fake-model")
    assert state.execution_status == "failed"
    assert events.events[-1].event_type == EventType.EXECUTION_FAILED
    _events_for(state, events)
