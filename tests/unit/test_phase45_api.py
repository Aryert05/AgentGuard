"""Cross-component API workflow tests for Phase 4.5."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from agentguard.api import ApiSettings, create_app
from agentguard.api.dependencies import AppServices
from agentguard.application.diagnosis_service import DiagnosisService
from agentguard.application.execution_service import ExecutionService
from agentguard.application.trace_service import TraceService
from agentguard.config.settings import Settings
from agentguard.diagnostics.service import DiagnosticService
from agentguard.schemas.events import EventCollector, EventType, ExecutionEvent
from agentguard.schemas.state import ExecutionState, ToolCall


class WorkflowRunner:
    """Deterministic fake execution boundary that emits a valid failure trace."""

    def __init__(self) -> None:
        self.state = ExecutionState(
            execution_id=str(uuid4()),
            task_id=str(uuid4()),
            user_input="private task password=task-secret",
            execution_status="failed",
            execution_mode="deterministic",
            start_time=datetime(2026, 11, 2, tzinfo=timezone.utc),
            end_time=datetime(2026, 11, 2, 0, 0, 2, tzinfo=timezone.utc),
            final_output="operation failed safely",
            tool_calls=[
                ToolCall(
                    tool_name="search",
                    arguments={"authorization": "private-argument"},
                    status="failed",
                    error="token=private-tool-error",
                )
            ],
        )
        self.calls = 0

    def run(self, task: str, *, mode: str, max_tool_calls: int):
        self.calls += 1
        events = EventCollector(self.state.execution_id, self.state.task_id)
        events.emit(
            ExecutionEvent(
                event_id="workflow-start",
                execution_id=self.state.execution_id,
                task_id=self.state.task_id,
                step_number=0,
                timestamp=self.state.start_time,
                event_type=EventType.EXECUTION_STARTED,
                component="agent",
                status="running",
            )
        )
        events.emit(
            ExecutionEvent(
                event_id="workflow-failure",
                execution_id=self.state.execution_id,
                task_id=self.state.task_id,
                step_number=1,
                timestamp=self.state.start_time + timedelta(seconds=1),
                event_type=EventType.TOOL_FAILED,
                component="tool",
                tool_name="search",
                status="failed",
                error_type="TimeoutError",
                error_message="token=private-event-token request timed out",
                metadata={"nested": {"api_key": "private-metadata"}},
            )
        )
        events.emit(
            ExecutionEvent(
                event_id="workflow-terminal",
                execution_id=self.state.execution_id,
                task_id=self.state.task_id,
                step_number=2,
                timestamp=self.state.end_time,
                event_type=EventType.EXECUTION_FAILED,
                component="agent",
                status="failed",
            )
        )
        return self.state, events


class WorkflowStore:
    """In-memory TraceReader and TraceWriter used to test API composition."""

    def __init__(self) -> None:
        self.execution: dict | None = None
        self.events: list[dict] = []
        self.write_count = 0

    def persist(self, state: ExecutionState, events: EventCollector) -> None:
        self.write_count += 1
        self.execution = {
            "execution": {
                "execution_id": state.execution_id,
                "task_id": state.task_id,
                "task_description": None,
                "execution_status": state.execution_status,
                "execution_mode": state.execution_mode,
                "started_at": state.start_time,
                "completed_at": state.end_time,
                "total_duration_ms": 2000.0,
                "successful_tool_call_count": 0,
                "failed_tool_call_count": 1,
                "metadata": {"password": "private-summary-secret"},
            }
        }
        self.events = [
            {**event.model_dump(mode="json"), "event_index": index}
            for index, event in enumerate(events.events)
        ]

    def get_execution(self, execution_id: str) -> dict | None:
        if self.execution is None:
            return None
        if self.execution["execution"]["execution_id"] != execution_id:
            return None
        return self.execution

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_event_index: int | None,
    ) -> dict | None:
        if self.get_execution(execution_id) is None:
            return None
        start = 0 if after_event_index is None else after_event_index + 1
        matching = [event for event in self.events if event["event_index"] >= start]
        page = matching[:limit]
        next_index = page[-1]["event_index"] if len(matching) > limit else None
        return {
            "execution_id": execution_id,
            "events": page,
            "next_after_event_index": next_index,
        }


def test_execution_retrieval_pagination_and_diagnosis_workflow() -> None:
    runner = WorkflowRunner()
    store = WorkflowStore()
    settings = ApiSettings(environment="test", docs_enabled=False)
    services = AppServices(
        settings=settings,
        execution_service=ExecutionService(
            runner,
            persistence_enabled=True,
            llm_configured=False,
            trace_writer=store,
        ),
        trace_service=TraceService(store, persistence_enabled=True),
        diagnosis_service=DiagnosisService(
            DiagnosticService(), store, persistence_enabled=True
        ),
    )
    app = create_app(settings, services=services)

    with TestClient(app) as client:
        health = client.get("/api/v1/health", headers={"X-Request-ID": "flow-health"})
        execution = client.post(
            "/api/v1/executions",
            json={"task": "perform a bounded search"},
            headers={"X-Request-ID": "flow-execution"},
        )
        execution_id = execution.json()["execution_id"]
        summary = client.get(
            f"/api/v1/executions/{execution_id}",
            headers={"X-Request-ID": "flow-summary"},
        )
        first_page = client.get(
            f"/api/v1/executions/{execution_id}/events",
            params={"limit": 1},
            headers={"X-Request-ID": "flow-events-1"},
        )
        second_page = client.get(
            f"/api/v1/executions/{execution_id}/events",
            params={"limit": 1, "after_event_index": first_page.json()["next_after_event_index"]},
            headers={"X-Request-ID": "flow-events-2"},
        )
        third_page = client.get(
            f"/api/v1/executions/{execution_id}/events",
            params={"limit": 1, "after_event_index": second_page.json()["next_after_event_index"]},
            headers={"X-Request-ID": "flow-events-3"},
        )
        diagnosis = client.post(
            f"/api/v1/executions/{execution_id}/diagnoses",
            headers={"X-Request-ID": "flow-diagnosis"},
        )

    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "flow-health"
    assert execution.status_code == 201
    assert execution.json()["persisted"] is True
    assert execution.json()["status"] == "failed"
    assert execution.headers["X-Request-ID"] == "flow-execution"
    assert summary.status_code == 200
    assert summary.json()["execution_id"] == execution_id
    assert summary.json()["metadata"]["password"] == "[REDACTED]"
    assert first_page.status_code == 200
    assert [item["event_index"] for item in first_page.json()["events"]] == [0]
    assert first_page.json()["next_after_event_index"] == 0
    assert second_page.status_code == 200
    assert [item["event_index"] for item in second_page.json()["events"]] == [1]
    assert second_page.json()["events"][0]["error_message"] == (
        "token=[REDACTED] request timed out"
    )
    assert second_page.json()["next_after_event_index"] == 1
    assert third_page.status_code == 200
    assert [item["event_index"] for item in third_page.json()["events"]] == [2]
    assert third_page.json()["next_after_event_index"] is None
    assert diagnosis.status_code == 200
    assert diagnosis.headers["X-Request-ID"] == "flow-diagnosis"
    assert diagnosis.json()["diagnosis"]["primary_category"] == "timeout"
    assert diagnosis.json()["recommendations"]
    assert runner.calls == 1
    assert store.write_count == 1
    for response in (
        execution,
        summary,
        first_page,
        second_page,
        third_page,
        diagnosis,
    ):
        for secret in (
            "task-secret",
            "private-argument",
            "private-tool-error",
            "private-event-token",
            "private-metadata",
            "private-summary-secret",
        ):
            assert secret not in response.text

