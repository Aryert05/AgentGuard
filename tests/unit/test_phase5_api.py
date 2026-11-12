from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentguard.api import ApiSettings, create_app
from agentguard.api.dependencies import AppServices
from agentguard.application.diagnosis_service import DiagnosisService
from agentguard.application.execution_service import ExecutionService
from agentguard.application.trace_service import DatabaseTraceReader, TraceService
from agentguard.database.models import ExecutionEventRecord, ExecutionRecord, Base
from agentguard.diagnostics.service import DiagnosticService


class FakeRunner:
    def run(self, task: str, *, mode: str, max_tool_calls: int):
        raise AssertionError("History and overview routes must not execute the agent")


def _build_test_app(reader, *, persistence_enabled: bool = True):
    settings = ApiSettings(environment="test", docs_enabled=False)
    services = AppServices(
        settings=settings,
        execution_service=ExecutionService(
            FakeRunner(),
            persistence_enabled=persistence_enabled,
            llm_configured=False,
        ),
        trace_service=TraceService(reader, persistence_enabled=persistence_enabled),
        diagnosis_service=DiagnosisService(
            DiagnosticService(), reader, persistence_enabled=persistence_enabled
        ),
    )
    return create_app(settings, services=services)


def test_executions_history_and_overview_without_persistence_returns_503():
    reader = DatabaseTraceReader(None)
    app = _build_test_app(reader, persistence_enabled=False)

    with TestClient(app) as client:
        history_resp = client.get("/api/v1/executions/history")
        overview_resp = client.get("/api/v1/executions/overview")

    assert history_resp.status_code == 503
    assert history_resp.json()["code"] == "persistence_not_configured"
    assert overview_resp.status_code == 503
    assert overview_resp.json()["code"] == "persistence_not_configured"


def test_executions_history_and_overview_with_persisted_records():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ExecutionRecord.__table__.create(engine)
    ExecutionEventRecord.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(
            ExecutionRecord(
                execution_id="exec-1",
                task_id="task-1",
                task_description="Search for quarterly earnings",
                execution_status="completed",
                execution_mode="deterministic",
                started_at=now,
                completed_at=now,
                total_duration_ms=120.0,
                successful_tool_call_count=1,
                failed_tool_call_count=0,
                metadata_json={},
            )
        )
        session.add(
            ExecutionRecord(
                execution_id="exec-2",
                task_id="task-2",
                task_description="Fetch weather data",
                execution_status="failed",
                execution_mode="deterministic",
                started_at=now,
                completed_at=now,
                total_duration_ms=450.0,
                successful_tool_call_count=0,
                failed_tool_call_count=1,
                metadata_json={},
            )
        )
        session.add(
            ExecutionEventRecord(
                event_id="evt-1",
                execution_id="exec-2",
                task_id="task-2",
                event_index=0,
                step_number=0,
                timestamp=now,
                event_type="tool_failed",
                component="tool",
                tool_name="weather",
                status="failed",
                duration_ms=450.0,
                error_type="TimeoutError",
                error_message="Gateway timeout",
                metadata_json={},
            )
        )

    reader = DatabaseTraceReader(factory)
    app = _build_test_app(reader, persistence_enabled=True)

    with TestClient(app) as client:
        # 1. Test history listing
        resp = client.get("/api/v1/executions/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert body["items"][0]["execution_id"] in ("exec-1", "exec-2")

        # 2. Test status filter
        filter_resp = client.get("/api/v1/executions/history?status=failed")
        assert filter_resp.status_code == 200
        assert filter_resp.json()["total"] == 1
        assert filter_resp.json()["items"][0]["execution_id"] == "exec-2"

        # 3. Test search filter
        search_resp = client.get("/api/v1/executions/history?query=earnings")
        assert search_resp.status_code == 200
        assert search_resp.json()["total"] == 1
        assert search_resp.json()["items"][0]["execution_id"] == "exec-1"

        # 4. Test overview metrics
        overview = client.get("/api/v1/executions/overview")
        assert overview.status_code == 200
        metrics = overview.json()
        assert metrics["total_executions"] == 2
        assert metrics["successful_executions"] == 1
        assert metrics["failed_executions"] == 1
        assert metrics["success_rate"] == 0.5
        assert metrics["failure_rate"] == 0.5
        assert metrics["status_distribution"] == {"completed": 1, "failed": 1}
        assert metrics["failure_category_distribution"] == {"TimeoutError": 1}
        assert len(metrics["recent_activity"]) == 1


def test_cors_headers_are_present():
    reader = DatabaseTraceReader(None)
    app = _build_test_app(reader, persistence_enabled=False)

    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/health",
            headers={"Origin": "http://localhost:5173"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
