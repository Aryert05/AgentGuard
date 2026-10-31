from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from agentguard.config.database import DatabaseSettings
from agentguard.config.settings import ConfigurationError
from agentguard.database.models import Base, ExecutionEventRecord, ExecutionRecord
from agentguard.database.repositories import TelemetryPersistenceError, _upsert_execution, persist_execution_trace
from agentguard.execution.runner import run_plan
from agentguard.agent.runtime import run_agent
from agentguard.schemas.events import EventCollector
from agentguard.telemetry.privacy import safe_task_description
from agentguard.telemetry.persistence import persist_if_configured


def test_postgresql_schema_has_keys_indexes_and_timezone_types():
    executions = Base.metadata.tables["executions"]
    events = Base.metadata.tables["execution_events"]
    assert executions.primary_key.columns.keys() == ["execution_id"]
    assert {fk.target_fullname for fk in events.foreign_keys} == {"executions.execution_id", "executions.task_id"}
    assert any("event_index" in constraint.columns.keys() for constraint in events.constraints if hasattr(constraint, "columns"))
    ddl = str(CreateTable(ExecutionEventRecord.__table__).compile(dialect=postgresql.dialect()))
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "FOREIGN KEY(execution_id, task_id)" in ddl
    assert any(index.name == "ix_execution_events_execution_time" for index in ExecutionEventRecord.__table__.indexes)


def test_database_settings_accept_postgres_and_reject_other_schemes(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/agentguard")
    monkeypatch.setenv("AGENTGUARD_STORE_TASK_TEXT", "false")
    settings = DatabaseSettings.from_env()
    assert settings.enabled and not settings.store_task_text
    assert settings.url == "postgresql+psycopg://user:pass@localhost/agentguard"
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/agentguard")
    assert DatabaseSettings.from_env().url == "postgresql+psycopg://user:pass@localhost/agentguard"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-postgres.db")
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        DatabaseSettings.from_env()
    monkeypatch.setenv("DATABASE_URL", "postgresql://broken url")
    with pytest.raises(ConfigurationError, match="valid SQLAlchemy URL"):
        DatabaseSettings.from_env()
    monkeypatch.delenv("DATABASE_URL")
    assert not DatabaseSettings.from_env().enabled


def test_explicit_agentguard_runtime_url_precedes_legacy_database_url(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_DATABASE_URL", "postgresql+psycopg://runtime:pass@localhost/runtime_db")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://legacy:pass@localhost/legacy_db")
    assert DatabaseSettings.from_env().url == "postgresql+psycopg://runtime:pass@localhost/runtime_db"
    monkeypatch.delenv("AGENTGUARD_DATABASE_URL")
    assert DatabaseSettings.from_env().url == "postgresql+psycopg://legacy:pass@localhost/legacy_db"


def test_database_settings_repr_never_contains_credentials():
    settings = DatabaseSettings("postgresql+psycopg://sensitive-user:sensitive-password@localhost/agentguard")
    assert "sensitive-user" not in repr(settings)
    assert "sensitive-password" not in repr(settings)


def test_engine_hides_sql_parameters():
    from agentguard.database.connection import create_session_factory
    factory = create_session_factory(DatabaseSettings("postgresql+psycopg://user:pass@localhost/db"))
    try:
        assert factory.kw["bind"].hide_parameters is True
    finally:
        factory.kw["bind"].dispose()


@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_execution_state_rejects_naive_timestamps(field):
    from agentguard.schemas.state import ExecutionState
    values = {"user_input": "safe task", field: datetime(2026, 1, 1)}
    with pytest.raises(ValueError, match="timezone-aware"):
        ExecutionState(**values)


def test_execution_state_accepts_aware_offsets_and_persistence_normalizes_to_utc():
    from agentguard.schemas.state import ExecutionState
    from datetime import timedelta
    start = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    end = datetime(2026, 1, 1, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    state = ExecutionState(user_input="safe task", start_time=start, end_time=end)

    class CaptureSession:
        statement = None
        def execute(self, statement):
            self.statement = statement

    capture = CaptureSession()
    _upsert_execution(capture, state, store_task_text=False)
    values = capture.statement.compile(dialect=postgresql.dialect()).params
    assert values["started_at"].utcoffset() == timedelta(0)
    assert values["completed_at"].utcoffset() == timedelta(0)
    assert values["total_duration_ms"] == 3_600_000


def test_persistence_rejects_mutated_naive_execution_timestamp_before_session():
    state, events = run_plan("calculate 1 plus 2", plan=[])
    object.__setattr__(state, "start_time", datetime(2026, 1, 1))
    with pytest.raises(TelemetryPersistenceError, match="naive execution start_time"):
        persist_execution_trace(object(), state, events)


def test_task_description_redacts_common_credentials_and_bounds_length():
    text = safe_task_description("find this token=secret-value and sk-proj-012345678901234567890123")
    assert "secret-value" not in text and "sk-proj-" not in text
    assert safe_task_description("x" * 2100) == "x" * 2000


def test_persistence_rejects_mismatched_ids_before_opening_session():
    state, events = run_plan("calculate 1 plus 2", plan=[])
    wrong = EventCollector("other-execution", state.task_id)
    with pytest.raises(TelemetryPersistenceError, match="identifiers"):
        persist_execution_trace(object(), state, wrong)


def test_execution_upsert_uses_postgres_conflict_update_and_omits_task_by_default():
    state, _ = run_plan("token=do-not-persist", plan=[])

    class CaptureSession:
        statement = None

        def execute(self, statement):
            self.statement = statement

    capture = CaptureSession()
    _upsert_execution(capture, state, store_task_text=False)
    compiled = str(capture.statement.compile(dialect=postgresql.dialect()))
    parameters = capture.statement.compile(dialect=postgresql.dialect()).params
    assert "ON CONFLICT (execution_id) DO UPDATE" in compiled
    assert parameters["task_description"] is None
    _upsert_execution(capture, state, store_task_text=True)
    parameters = capture.statement.compile(dialect=postgresql.dialect()).params
    assert "[REDACTED]" in parameters["task_description"]
    assert state.start_time.tzinfo is not None


def test_offline_execution_does_not_construct_a_database_engine(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("agentguard.telemetry.persistence.create_session_factory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected connection")))
    state, _ = run_agent("calculate 1 plus 2", demo=True)
    assert state.execution_status == "completed"


def test_configured_persistence_failure_is_not_hidden(monkeypatch):
    from agentguard.database.repositories import TelemetryPersistenceError
    class FakeEngine:
        def dispose(self):
            pass
    class FakeFactory:
        kw = {"bind": FakeEngine()}
    monkeypatch.setattr("agentguard.telemetry.persistence.create_session_factory", lambda *_: FakeFactory())
    monkeypatch.setattr("agentguard.telemetry.persistence.persist_execution_trace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TelemetryPersistenceError("database down")))
    state, events = run_plan("calculate 1 plus 2")
    with pytest.raises(TelemetryPersistenceError, match="database down"):
        persist_if_configured(state, events, DatabaseSettings("postgresql+psycopg://user:pass@localhost/db"))
