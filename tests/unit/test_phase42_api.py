from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from agentguard.api import ApiSettings, create_app
from agentguard.api.dependencies import AppServices
from agentguard.application.execution_service import AgentExecutionRunner, ExecutionService
from agentguard.application.errors import PersistenceUnavailableError
from agentguard.application.diagnosis_service import DiagnosisService as ApiDiagnosisService
from agentguard.application.trace_service import DatabaseTraceReader, TraceService
from agentguard.config.database import DatabaseSettings
from agentguard.config.settings import ConfigurationError, Settings
from agentguard.database.models import ExecutionEventRecord, ExecutionRecord
from agentguard.database.repositories import (
    TelemetryPersistenceError,
    retrieve_execution_events,
)
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState, ToolCall
from agentguard.diagnostics.service import DiagnosticService


def _state(
    *,
    status: str = "completed",
    mode: str = "deterministic",
    final_output: str | None = "3750",
    tool_status: str = "completed",
) -> ExecutionState:
    started = datetime.now(timezone.utc)
    return ExecutionState(
        execution_id=str(uuid4()),
        task_id=str(uuid4()),
        user_input="private task text",
        execution_status=status,
        execution_mode=mode,
        start_time=started,
        end_time=started + timedelta(milliseconds=12),
        final_output=final_output,
        tool_calls=[
            ToolCall(
                tool_name="calculator",
                arguments={"private_argument": "secret-argument"},
                status=tool_status,
                result="private tool result",
                error="api_key=secret-tool-error" if tool_status == "failed" else None,
            )
        ],
    )


class FakeRunner:
    def __init__(
        self,
        state: ExecutionState | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.state = state or _state()
        self.error = error
        self.calls: list[dict] = []

    def run(self, task: str, *, mode, max_tool_calls: int):
        self.calls.append(
            {"task": task, "mode": mode, "max_tool_calls": max_tool_calls}
        )
        if self.error is not None:
            raise self.error
        return self.state, EventCollector(self.state.execution_id, self.state.task_id)


class FakeReader:
    def __init__(
        self,
        *,
        execution: dict | None = None,
        events: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.execution = execution
        self.events = events
        self.error = error
        self.event_calls: list[dict] = []

    def get_execution(self, execution_id: str):
        if self.error is not None:
            raise self.error
        return self.execution

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_event_index: int | None,
    ):
        if self.error is not None:
            raise self.error
        self.event_calls.append(
            {
                "execution_id": execution_id,
                "limit": limit,
                "after_event_index": after_event_index,
            }
        )
        return self.events


class FakeWriter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[ExecutionState, EventCollector]] = []

    def persist(self, state: ExecutionState, events: EventCollector) -> None:
        self.calls.append((state, events))
        if self.error is not None:
            raise self.error


def _build_app(
    *,
    runner: FakeRunner | AgentExecutionRunner | None = None,
    reader: FakeReader | DatabaseTraceReader | None = None,
    writer: FakeWriter | None = None,
    persistence_enabled: bool = False,
    llm_configured: bool = False,
):
    settings = ApiSettings(environment="test", docs_enabled=True)
    execution_runner = runner or FakeRunner()
    trace_reader = reader or FakeReader()
    trace_writer = writer or (FakeWriter() if persistence_enabled else None)
    services = AppServices(
        settings=settings,
        execution_service=ExecutionService(
            execution_runner,
            persistence_enabled=persistence_enabled,
            llm_configured=llm_configured,
            trace_writer=trace_writer,
        ),
        trace_service=TraceService(
            trace_reader,
            persistence_enabled=persistence_enabled,
        ),
        diagnosis_service=ApiDiagnosisService(
            DiagnosticService(),
            trace_reader,
            persistence_enabled=persistence_enabled,
        ),
    )
    return create_app(settings, services=services), execution_runner, trace_reader


def _execution_record(execution_id: str) -> dict:
    started = datetime.now(timezone.utc)
    return {
        "execution_id": execution_id,
        "task_id": str(uuid4()),
        "task_description": "task password=secret-task-password",
        "execution_status": "failed",
        "execution_mode": "deterministic",
        "started_at": started,
        "completed_at": started + timedelta(milliseconds=8),
        "total_duration_ms": 8.0,
        "successful_tool_call_count": 0,
        "failed_tool_call_count": 1,
        "metadata": {
            "failure_mode": None,
            "current_step": 1,
            "api_key": "secret-metadata-value",
        },
    }


def _event_record(execution_id: str, *, event_index: int) -> dict:
    return {
        "event_id": str(uuid4()),
        "execution_id": execution_id,
        "task_id": str(uuid4()),
        "event_index": event_index,
        "step_number": 1,
        "timestamp": datetime.now(timezone.utc),
        "event_type": "tool_failed",
        "component": "tool",
        "tool_name": "calculator",
        "status": "failed",
        "duration_ms": 2.0,
        "error_type": "RuntimeError",
        "error_message": "token=secret-event-token",
        "metadata": {"authorization": "secret-event-metadata"},
    }


def test_deterministic_execution_uses_existing_agent_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        DatabaseSettings,
        "from_env",
        classmethod(lambda cls: pytest.fail("offline API execution must not read DB settings")),
    )
    runner = AgentExecutionRunner(Settings(provider=None, model=None, failure_mode=None))
    app, _, _ = _build_app(runner=runner)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            json={
                "task": "Calculate the total cost of 15 products priced at 250 each",
                "max_tool_calls": 2,
            },
            headers={"X-Request-ID": "execution-success-id"},
        )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "execution-success-id"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    UUID(body["execution_id"])
    UUID(body["task_id"])
    assert body["status"] == "completed"
    assert body["mode"] == "deterministic"
    assert body["duration_ms"] >= 0
    assert body["successful_tool_call_count"] == 1
    assert body["failed_tool_call_count"] == 0
    assert body["tool_calls"] == [
        {"tool_name": "calculator", "status": "completed", "duration_ms": None}
    ]
    assert "3750" in body["final_output"]
    assert body["persisted"] is False


def test_agent_execution_runner_disables_runtime_owned_persistence(monkeypatch) -> None:
    state = _state()
    events = EventCollector(state.execution_id, state.task_id)
    calls = []

    def fake_run_agent(*args, **kwargs):
        calls.append(kwargs)
        return state, events

    monkeypatch.setattr("agentguard.application.execution_service.run_agent", fake_run_agent)
    runner = AgentExecutionRunner(Settings(provider=None, model=None, failure_mode=None))
    runner.run("task", mode="deterministic", max_tool_calls=2)

    assert calls[0]["persist"] is False
    assert calls[0]["max_tool_calls"] == 2


def test_database_trace_writer_delegates_to_phase2_repository(monkeypatch) -> None:
    from agentguard.application.execution_service import DatabaseTraceWriter

    state = _state()
    events = EventCollector(state.execution_id, state.task_id)
    factory = object()
    calls = []

    def fake_persist(session_factory, saved_state, saved_events, *, store_task_text):
        calls.append((session_factory, saved_state, saved_events, store_task_text))

    monkeypatch.setattr(
        "agentguard.database.repositories.persist_execution_trace", fake_persist
    )
    DatabaseTraceWriter(factory, store_task_text=True).persist(state, events)
    assert calls == [(factory, state, events, True)]


def test_api_lifespan_shares_one_session_factory_and_disposes_it(monkeypatch) -> None:
    from agentguard.api.main import create_app as create_default_app

    class Bind:
        disposed = False

        def dispose(self):
            self.disposed = True

    bind = Bind()
    factory = SimpleNamespace(kw={"bind": bind})
    created = []

    monkeypatch.setattr(
        "agentguard.api.main.DatabaseSettings.from_env",
        classmethod(lambda cls: DatabaseSettings(url="postgresql+psycopg://runtime@db/agentguard")),
    )
    monkeypatch.setattr(
        "agentguard.api.main.load_settings",
        lambda: Settings(provider=None, model=None, failure_mode=None),
    )
    monkeypatch.setattr(
        "agentguard.api.main.create_session_factory",
        lambda settings: created.append(settings) or factory,
    )
    app = create_default_app(ApiSettings(environment="test", docs_enabled=False))

    with TestClient(app):
        services = app.state.services
        assert services.trace_service.reader.session_factory is factory
        assert services.execution_service.trace_writer.session_factory is factory
        assert services.execution_service.trace_writer.store_task_text is False
    assert created and bind.disposed is True


def test_offline_app_lifespan_never_creates_database_factory(monkeypatch) -> None:
    from agentguard.api.main import create_app as create_default_app

    monkeypatch.setattr(
        "agentguard.api.main.DatabaseSettings.from_env",
        classmethod(lambda cls: DatabaseSettings(url=None)),
    )
    monkeypatch.setattr(
        "agentguard.api.main.create_session_factory",
        lambda *_args, **_kwargs: pytest.fail("offline mode must not create an engine"),
    )
    app = create_default_app(ApiSettings(environment="test", docs_enabled=False))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            json={"task": "Calculate 2 plus 3", "mode": "deterministic"},
        )
    assert response.status_code == 201
    assert response.json()["persisted"] is False


def test_execution_response_omits_internal_state_and_redacts_output() -> None:
    state = _state(final_output="api_key=secret-final-output")
    runner = FakeRunner(state)
    app, _, _ = _build_app(runner=runner)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            json={"task": "calculate 2 + 3", "max_tool_calls": 3},
        )

    body = response.json()
    assert response.status_code == 201
    assert body["final_output"] == "api_key=[REDACTED]"
    assert runner.calls == [
        {"task": "calculate 2 + 3", "mode": "deterministic", "max_tool_calls": 3}
    ]
    for hidden in (
        "private task text",
        "private_argument",
        "secret-argument",
        "private tool result",
        "user_input",
        "messages",
        "arguments",
        "failure_mode",
    ):
        assert hidden not in response.text


def test_failed_execution_returns_201_with_failed_status() -> None:
    state = _state(
        status="failed",
        final_output="Execution failed safely",
        tool_status="failed",
    )
    app, _, _ = _build_app(runner=FakeRunner(state))

    with TestClient(app) as client:
        response = client.post("/api/v1/executions", json={"task": "fail this"})

    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "failed"
    assert body["successful_tool_call_count"] == 0
    assert body["failed_tool_call_count"] == 1
    assert body["tool_calls"][0]["status"] == "failed"
    assert body["final_output"] == "Execution failed safely"
    assert "secret-tool-error" not in response.text


def test_persisted_flag_reflects_application_configuration() -> None:
    writer = FakeWriter()
    app, runner, _ = _build_app(persistence_enabled=True, writer=writer)

    with TestClient(app) as client:
        response = client.post("/api/v1/executions", json={"task": "calculate 2 + 3"})

    assert response.status_code == 201
    assert response.json()["persisted"] is True
    assert len(writer.calls) == 1
    assert writer.calls[0][0] is runner.state
    assert writer.calls[0][1].execution_id == runner.state.execution_id


def test_configured_persistence_without_writer_fails_closed() -> None:
    # Do not claim persistence when a configured deployment lacks its writer.
    from agentguard.api.schemas.execution import ExecutionRequest

    runner = FakeRunner()
    execution_service = ExecutionService(
        runner, persistence_enabled=True, llm_configured=False
    )
    with pytest.raises(PersistenceUnavailableError):
        execution_service.run(ExecutionRequest(task="calculate 2 + 3"))
    assert runner.calls == []


def test_writer_failure_returns_sanitized_503() -> None:
    runner = FakeRunner()
    writer = FakeWriter(
        error=TelemetryPersistenceError(
            "postgresql://agentguard:secret-password@database/agentguard"
        )
    )
    app, _, _ = _build_app(runner=runner, writer=writer, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.post("/api/v1/executions", json={"task": "calculate 2 + 3"})

    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "secret-password" not in response.text
    assert "postgresql://" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"task": ""},
        {"task": "   "},
        {"task": "x" * 8001},
        {"task": "calculate", "max_tool_calls": 0},
        {"task": "calculate", "max_tool_calls": 9},
        {"task": "calculate", "mode": "unknown"},
        {"task": "calculate", "failure_mode": "timeout"},
    ],
)
def test_invalid_execution_requests_are_rejected(payload) -> None:
    runner = FakeRunner()
    app, _, _ = _build_app(runner=runner)

    with TestClient(app) as client:
        response = client.post("/api/v1/executions", json=payload)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_error"
    assert runner.calls == []


def test_malformed_execution_json_returns_400_without_echo() -> None:
    app, _, _ = _build_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            content='{"task": "secret-malformed-task"',
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "secret-malformed-task" not in response.text


def test_llm_mode_without_configuration_returns_503() -> None:
    runner = FakeRunner()
    app, _, _ = _build_app(runner=runner, llm_configured=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            json={"task": "summarize Tata Motors", "mode": "llm"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "llm_not_configured"
    assert runner.calls == []


def test_llm_mode_with_configuration_delegates_to_runner() -> None:
    runner = FakeRunner(_state(mode="llm", final_output="configured response"))
    app, _, _ = _build_app(runner=runner, llm_configured=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/executions",
            json={"task": "summarize Tata Motors", "mode": "llm", "max_tool_calls": 4},
        )

    assert response.status_code == 201
    assert response.json()["mode"] == "llm"
    assert runner.calls == [
        {"task": "summarize Tata Motors", "mode": "llm", "max_tool_calls": 4}
    ]


def test_persistence_failure_returns_sanitized_503() -> None:
    runner = FakeRunner(
        error=TelemetryPersistenceError(
            "postgresql://agentguard:secret-password@database/agentguard"
        )
    )
    app, _, _ = _build_app(runner=runner, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.post("/api/v1/executions", json={"task": "calculate 2 + 3"})

    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "secret-password" not in response.text
    assert "postgresql://" not in response.text


def test_unexpected_execution_error_returns_generic_500() -> None:
    runner = FakeRunner(error=RuntimeError("private-runtime-detail"))
    app, _, _ = _build_app(runner=runner)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/executions", json={"task": "calculate 2 + 3"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "private-runtime-detail" not in response.text


def test_runner_initialization_failures_return_sanitized_503() -> None:
    import_failure = RuntimeError("missing provider integration")
    import_failure.__cause__ = ImportError("private-package-name")
    errors = [
        ConfigurationError("private configuration detail"),
        ImportError("private-package-name"),
        import_failure,
    ]

    for error in errors:
        app, _, _ = _build_app(runner=FakeRunner(error=error), llm_configured=True)

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/executions",
                json={"task": "summarize Tata Motors", "mode": "llm"},
            )

        assert response.status_code == 503
        assert response.json()["code"] == "execution_unavailable"
        assert "private" not in response.text


def test_injected_services_are_isolated_between_apps() -> None:
    app_a, runner_a, _ = _build_app(runner=FakeRunner(_state(final_output="A")))
    app_b, runner_b, _ = _build_app(runner=FakeRunner(_state(final_output="B")))

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        response_a = client_a.post("/api/v1/executions", json={"task": "a"})
        response_b = client_b.post("/api/v1/executions", json={"task": "b"})

    assert response_a.json()["final_output"] == "A"
    assert response_b.json()["final_output"] == "B"
    assert len(runner_a.calls) == 1
    assert len(runner_b.calls) == 1


def test_get_execution_summary_maps_and_redacts_persisted_fields() -> None:
    execution_id = str(uuid4())
    reader = FakeReader(execution={"execution": _execution_record(execution_id)})
    app, _, _ = _build_app(reader=reader, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{execution_id}")

    body = response.json()
    assert response.status_code == 200
    assert body["execution_id"] == execution_id
    assert body["status"] == "failed"
    assert body["mode"] == "deterministic"
    assert body["duration_ms"] == 8.0
    assert body["task_description"] == "task password=[REDACTED]"
    assert body["metadata"]["api_key"] == "[REDACTED]"
    assert "secret-task-password" not in response.text
    assert "secret-metadata-value" not in response.text
    assert "final_output" not in body


def test_get_execution_unknown_returns_404() -> None:
    app, _, _ = _build_app(reader=FakeReader(), persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "execution_not_found"


def test_get_execution_without_persistence_returns_503() -> None:
    reader = FakeReader()
    app, _, _ = _build_app(reader=reader, persistence_enabled=False)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{uuid4()}")

    assert response.status_code == 503
    assert response.json()["code"] == "persistence_not_configured"
    assert reader.event_calls == []


def test_get_execution_invalid_identifier_returns_422() -> None:
    app, _, _ = _build_app(persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get("/api/v1/executions/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_database_read_failure_returns_sanitized_503(monkeypatch) -> None:
    def fail_read(session_factory, execution_id):
        raise SQLAlchemyError(
            "postgresql://agentguard:secret-db-password@database/agentguard"
        )

    monkeypatch.setattr(
        "agentguard.application.trace_service.retrieve_execution_trace",
        fail_read,
    )
    reader = DatabaseTraceReader(object())
    app, _, _ = _build_app(reader=reader, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{uuid4()}")

    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"
    assert "secret-db-password" not in response.text
    assert "postgresql://" not in response.text


def test_get_events_returns_ordered_bounded_page() -> None:
    execution_id = str(uuid4())
    reader = FakeReader(
        events={
            "execution_id": execution_id,
            "events": [
                _event_record(execution_id, event_index=3),
                _event_record(execution_id, event_index=4),
            ],
            "next_after_event_index": 4,
        }
    )
    app, _, _ = _build_app(reader=reader, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/executions/{execution_id}/events",
            params={"limit": 2, "after_event_index": 2},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["execution_id"] == execution_id
    assert [event["event_index"] for event in body["events"]] == [3, 4]
    assert body["next_after_event_index"] == 4
    assert body["events"][0]["event_type"] == "tool_failed"
    assert body["events"][0]["error_message"] == "token=[REDACTED]"
    assert body["events"][0]["metadata"]["authorization"] == "[REDACTED]"
    assert "secret-event-token" not in response.text
    assert "secret-event-metadata" not in response.text
    assert reader.event_calls == [
        {
            "execution_id": execution_id,
            "limit": 2,
            "after_event_index": 2,
        }
    ]


def test_get_events_empty_last_page_has_null_cursor() -> None:
    execution_id = str(uuid4())
    reader = FakeReader(
        events={
            "execution_id": execution_id,
            "events": [],
            "next_after_event_index": None,
        }
    )
    app, _, _ = _build_app(reader=reader, persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{execution_id}/events")

    assert response.status_code == 200
    assert response.json()["events"] == []
    assert response.json()["next_after_event_index"] is None
    assert reader.event_calls[0]["limit"] == 100
    assert reader.event_calls[0]["after_event_index"] is None


def test_get_events_unknown_execution_returns_404() -> None:
    app, _, _ = _build_app(reader=FakeReader(), persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{uuid4()}/events")

    assert response.status_code == 404
    assert response.json()["code"] == "execution_not_found"


def test_get_events_without_persistence_returns_503() -> None:
    reader = FakeReader()
    app, _, _ = _build_app(reader=reader, persistence_enabled=False)

    with TestClient(app) as client:
        response = client.get(f"/api/v1/executions/{uuid4()}/events")

    assert response.status_code == 503
    assert response.json()["code"] == "persistence_not_configured"
    assert reader.event_calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"after_event_index": -1},
    ],
)
def test_invalid_event_query_parameters_return_422(params) -> None:
    app, _, _ = _build_app(persistence_enabled=True)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/executions/{uuid4()}/events",
            params=params,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_phase41_health_and_openapi_regression() -> None:
    app, _, _ = _build_app()

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        openapi = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json()["checks"] == {"application": "ok"}
    paths = openapi.json()["paths"]
    assert "/api/v1/health" in paths
    assert "post" in paths["/api/v1/executions"]
    assert "get" in paths["/api/v1/executions/{execution_id}"]
    assert "get" in paths["/api/v1/executions/{execution_id}/events"]
    assert "get" not in paths["/api/v1/executions"]


def test_repository_event_pagination_is_ordered_and_bounded() -> None:
    engine = create_engine("sqlite://")
    ExecutionRecord.__table__.create(engine)
    ExecutionEventRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    execution_id = str(uuid4())
    task_id = str(uuid4())
    started = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(
            ExecutionRecord(
                execution_id=execution_id,
                task_id=task_id,
                execution_status="completed",
                execution_mode="deterministic",
                started_at=started,
                completed_at=started,
                total_duration_ms=1.0,
                successful_tool_call_count=4,
                failed_tool_call_count=0,
                metadata_json={},
            )
        )
        for event_index in range(4):
            session.add(
                ExecutionEventRecord(
                    event_id=str(uuid4()),
                    execution_id=execution_id,
                    task_id=task_id,
                    event_index=event_index,
                    step_number=event_index,
                    timestamp=started + timedelta(milliseconds=event_index),
                    event_type="tool_completed",
                    component="tool",
                    tool_name="calculator",
                    status="completed",
                    duration_ms=1.0,
                    metadata_json={"event_index": event_index},
                )
            )

    first = retrieve_execution_events(factory, execution_id, limit=2)
    second = retrieve_execution_events(
        factory, execution_id, limit=2, after_event_index=1
    )
    last = retrieve_execution_events(
        factory, execution_id, limit=2, after_event_index=3
    )

    assert [event["event_index"] for event in first["events"]] == [0, 1]
    assert first["next_after_event_index"] == 1
    assert [event["event_index"] for event in second["events"]] == [2, 3]
    assert second["next_after_event_index"] is None
    assert last["events"] == []
    assert last["next_after_event_index"] is None
    assert retrieve_execution_events(factory, str(uuid4()), limit=2) is None
