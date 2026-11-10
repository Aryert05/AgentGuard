from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from agentguard.api import ApiSettings, create_app
from agentguard.api.dependencies import AppServices
from agentguard.application.diagnosis_service import DiagnosisService as ApiDiagnosisService
from agentguard.application.execution_service import ExecutionService
from agentguard.application.trace_service import TraceService
from agentguard.config.settings import Settings
from agentguard.diagnostics import (
    BoundedRecoveryController,
    DiagnosticService,
    FailureCategory,
    MLRiskSignal,
    RecoveryAction,
)
from agentguard.schemas.events import EventType, ExecutionEvent

BASE_TIME = datetime(2026, 11, 2, tzinfo=timezone.utc)


def _event(
    event_id: str,
    index: int,
    event_type: EventType,
    *,
    status: str,
    tool: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=event_id,
        execution_id="execution-1",
        task_id="task-1",
        step_number=index,
        timestamp=BASE_TIME + timedelta(milliseconds=index),
        event_type=event_type,
        component="tool" if tool else "agent",
        tool_name=tool,
        status=status,
        error_type=error_type,
        error_message=error_message,
        metadata=metadata or {},
    )


def _failure_prefix() -> list[ExecutionEvent]:
    return [
        _event("evt-1", 0, EventType.EXECUTION_STARTED, status="running"),
        _event("evt-2", 1, EventType.TOOL_SELECTED, status="selected", tool="search"),
        _event(
            "evt-3",
            2,
            EventType.TOOL_FAILED,
            status="failed",
            tool="search",
            error_type="TimeoutError",
            error_message="operation timed out",
        ),
    ]


class FakeRunner:
    def run(self, task: str, *, mode: str, max_tool_calls: int):
        raise AssertionError("Diagnosis routes must not execute the agent")


class FakeTraceReader:
    def __init__(self, page: dict | None = None, error: Exception | None = None):
        self.page = page
        self.error = error
        self.calls: list[dict] = []

    def get_execution(self, execution_id: str):
        raise AssertionError("Diagnosis must use bounded event retrieval")

    def list_events(self, execution_id: str, *, limit: int, after_event_index: int | None):
        self.calls.append(
            {
                "execution_id": execution_id,
                "limit": limit,
                "after_event_index": after_event_index,
            }
        )
        if self.error is not None:
            raise self.error
        return self.page


def _app(
    *,
    reader: FakeTraceReader | None = None,
    persistence_enabled: bool = False,
    scorer=None,
):
    trace_reader = reader or FakeTraceReader()
    settings = ApiSettings(environment="test", docs_enabled=False)
    services = AppServices(
        settings=settings,
        execution_service=ExecutionService(
            FakeRunner(), persistence_enabled=False, llm_configured=False
        ),
        trace_service=TraceService(
            trace_reader, persistence_enabled=persistence_enabled
        ),
        diagnosis_service=ApiDiagnosisService(
            DiagnosticService(ml_scorer=scorer),
            trace_reader,
            persistence_enabled=persistence_enabled,
        ),
    )
    return create_app(settings, services=services), trace_reader


def _payload(events: list[ExecutionEvent]) -> dict:
    return {"events": [event.model_dump(mode="json") for event in events]}


def test_stateless_diagnosis_returns_pattern_and_informational_recommendation():
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", json=_payload(_failure_prefix()))

    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"]["primary_category"] == FailureCategory.TIMEOUT.value
    assert body["recommendations"][0]["action"] == RecoveryAction.RETRY_TOOL.value
    assert body["recommendations"][0]["evidence_event_ids"] == ["evt-3"]
    assert body["ml_error"] is None
    assert "attempts" not in body and "recovery_session" not in body


def test_empty_and_streaming_prefixes_follow_phase34_semantics():
    app, _ = _app()
    streaming = [_event("evt-1", 0, EventType.EXECUTION_STARTED, status="running")]
    with TestClient(app) as client:
        empty_response = client.post("/api/v1/diagnoses", json={"events": []})
        streaming_response = client.post(
            "/api/v1/diagnoses", json=_payload(streaming)
        )

    assert empty_response.status_code == 200
    assert empty_response.json()["diagnosis"]["primary_category"] == "unknown"
    assert streaming_response.status_code == 200
    assert streaming_response.json()["diagnosis"]["is_failure"] is False
    assert streaming_response.json()["diagnosis"]["terminal_observed"] is False


def test_invalid_event_payload_and_invalid_prefix_use_problem_details():
    app, _ = _app()
    bad_event = {"event_id": "evt-1", "event_type": "not-an-event"}
    no_start = _failure_prefix()[1:]
    with TestClient(app) as client:
        schema_error = client.post("/api/v1/diagnoses", json={"events": [bad_event]})
        prefix_error = client.post("/api/v1/diagnoses", json=_payload(no_start))

    assert schema_error.status_code == 422
    assert schema_error.json()["code"] == "validation_error"
    assert prefix_error.status_code == 422
    assert prefix_error.json()["code"] == "invalid_diagnosis_prefix"
    assert prefix_error.headers["X-Request-ID"]


def test_client_cannot_supply_ml_signal_or_prediction_values():
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/diagnoses",
            json={**_payload(_failure_prefix()), "ml_signal": {"probability": 1.0}},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_server_scorer_success_is_included_in_api_diagnosis():
    class Scorer:
        def score(self, events):
            return MLRiskSignal(probability=0.8, threshold=0.6, model_name="server-model")

    app, _ = _app(scorer=Scorer())
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", json=_payload(_failure_prefix()))

    assert response.status_code == 200
    assert response.json()["diagnosis"]["ml_risk"] == {
        "probability": 0.8,
        "threshold": 0.6,
        "model_name": "server-model",
        "source": "provided",
        "predicted_failure": True,
    }


def test_scorer_failure_returns_rule_diagnosis_and_safe_ml_error():
    class BrokenScorer:
        def score(self, events):
            raise RuntimeError("private connection string password=secret-value")

    app, _ = _app(scorer=BrokenScorer())
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", json=_payload(_failure_prefix()))

    assert response.status_code == 200
    body = response.json()
    assert body["diagnosis"]["primary_category"] == "timeout"
    assert body["diagnosis"]["ml_risk"] is None
    assert body["ml_error"]
    assert "secret-value" not in response.text
    assert "RuntimeError" not in response.text


def test_diagnosis_response_redacts_untrusted_event_error_text():
    events = [
        _event("evt-1", 0, EventType.EXECUTION_STARTED, status="running"),
        _event(
            "evt-2",
            1,
            EventType.TOOL_FAILED,
            status="failed",
            tool="search",
            error_type="password=secret-value",
            error_message="unclassified failure",
        ),
    ]
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", json=_payload(events))
    assert response.status_code == 200
    assert "secret-value" not in response.text


def test_persisted_execution_diagnosis_uses_bounded_event_reader():
    execution_id = str(uuid4())
    events = _failure_prefix()
    page = {
        "execution_id": execution_id,
        "events": [
            {**event.model_dump(mode="json"), "event_index": index}
            for index, event in enumerate(events)
        ],
        "next_after_event_index": None,
    }
    reader = FakeTraceReader(page=page)
    app, _ = _app(reader=reader, persistence_enabled=True)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/executions/{execution_id}/diagnoses")

    assert response.status_code == 200
    assert response.json()["diagnosis"]["primary_category"] == "timeout"
    assert reader.calls == [
        {"execution_id": execution_id, "limit": 1000, "after_event_index": None}
    ]


def test_persisted_execution_diagnosis_not_found_and_disabled_errors():
    missing_reader = FakeTraceReader(page=None)
    missing_app, _ = _app(reader=missing_reader, persistence_enabled=True)
    disabled_app, _ = _app(persistence_enabled=False)
    execution_id = str(uuid4())
    with TestClient(missing_app) as client:
        missing = client.post(f"/api/v1/executions/{execution_id}/diagnoses")
    with TestClient(disabled_app) as client:
        disabled = client.post(f"/api/v1/executions/{execution_id}/diagnoses")

    assert missing.status_code == 404
    assert missing.json()["code"] == "execution_not_found"
    assert disabled.status_code == 503
    assert disabled.json()["code"] == "persistence_unavailable"


def test_persisted_execution_diagnosis_maps_reader_failure_to_safe_503():
    from agentguard.application.errors import PersistenceUnavailableError

    reader = FakeTraceReader(error=PersistenceUnavailableError())
    app, _ = _app(reader=reader, persistence_enabled=True)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/executions/{uuid4()}/diagnoses")
    assert response.status_code == 503
    assert response.json()["code"] == "persistence_unavailable"


def test_persisted_execution_diagnosis_rejects_more_than_event_cap():
    execution_id = str(uuid4())
    reader = FakeTraceReader(
        page={"execution_id": execution_id, "events": [], "next_after_event_index": 999}
    )
    app, _ = _app(reader=reader, persistence_enabled=True)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/executions/{execution_id}/diagnoses")
    assert response.status_code == 413
    assert response.json()["code"] == "diagnosis_trace_too_large"


def test_diagnosis_never_invokes_recovery_controller(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnosis API must not invoke recovery")

    monkeypatch.setattr(BoundedRecoveryController, "recover", forbidden)
    app, _ = _app()
    with TestClient(app) as client:
        response = client.post("/api/v1/diagnoses", json=_payload(_failure_prefix()))
    assert response.status_code == 200
    assert response.json()["recommendations"]
