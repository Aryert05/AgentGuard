"""PostgreSQL-only persistence tests; skipped unless a migrated test DB is configured."""
from __future__ import annotations

import importlib.util
import os
from uuid import uuid4

import pytest

TEST_URL = os.getenv("AGENTGUARD_TEST_DATABASE_URL")
MIGRATION_URL = os.getenv("AGENTGUARD_TEST_MIGRATION_DATABASE_URL")
DEPS_AVAILABLE = importlib.util.find_spec("psycopg") and importlib.util.find_spec("alembic")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not (TEST_URL and MIGRATION_URL and DEPS_AVAILABLE),
    reason="Set runtime and migration test database URLs and install psycopg/alembic")]


def test_postgres_persistence_idempotency_conflicts_fk_and_rollback(monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, delete, select
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.orm import sessionmaker

    from agentguard.database.models import ExecutionEventRecord, ExecutionRecord
    from agentguard.database.repositories import TelemetryPersistenceError, persist_execution_trace, retrieve_execution_trace
    from agentguard.execution.runner import run_plan
    from agentguard.schemas.events import EventCollector

    monkeypatch.setenv("AGENTGUARD_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("AGENTGUARD_MIGRATION_DATABASE_URL", MIGRATION_URL)
    alembic = Config("alembic.ini")
    alembic.set_main_option("sqlalchemy.url", MIGRATION_URL.replace("%", "%%"))
    command.upgrade(alembic, "head")
    engine = create_engine(TEST_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    admin_engine = create_engine(MIGRATION_URL, pool_pre_ping=True, hide_parameters=True)
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    from sqlalchemy import text
    with admin_engine.connect() as connection:
        migration_role = connection.execute(text("""
            SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                   has_database_privilege(current_user, current_database(), 'CREATE'),
                   has_database_privilege(current_user, current_database(), 'TEMP'),
                   has_schema_privilege(current_user, 'public', 'CREATE')
            FROM pg_roles AS r WHERE r.rolname = current_user
        """)).one()
        assert tuple(migration_role) == (False, False, False, False, False, True)
    from sqlalchemy.engine import make_url
    runtime_role = make_url(TEST_URL).username
    quoted_role = admin_engine.dialect.identifier_preparer.quote(runtime_role)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
        connection.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE ON TABLE executions TO {quoted_role}")
        connection.exec_driver_sql(f"GRANT SELECT, INSERT ON TABLE execution_events TO {quoted_role}")
        connection.exec_driver_sql("CREATE TABLE agentguard_runtime_privilege_probe (id integer)")
    state, events = run_plan("Calculate 2 plus 3")
    persisted_ids = [state.execution_id]
    try:
        with factory() as session:
            role_state = session.execute(text("""
                SELECT r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                       has_database_privilege(current_user, current_database(), 'CONNECT'),
                       has_database_privilege(current_user, current_database(), 'CREATE'),
                       has_database_privilege(current_user, current_database(), 'TEMP'),
                       has_schema_privilege(current_user, 'public', 'USAGE'),
                       has_schema_privilege(current_user, 'public', 'CREATE'),
                       has_table_privilege(current_user, 'executions', 'SELECT'),
                       has_table_privilege(current_user, 'executions', 'INSERT'),
                       has_table_privilege(current_user, 'executions', 'UPDATE'),
                       has_table_privilege(current_user, 'executions', 'DELETE'),
                       has_table_privilege(current_user, 'execution_events', 'SELECT'),
                       has_table_privilege(current_user, 'execution_events', 'INSERT'),
                       has_table_privilege(current_user, 'execution_events', 'UPDATE'),
                       has_table_privilege(current_user, 'execution_events', 'DELETE')
                FROM pg_roles AS r WHERE r.rolname = current_user
            """)).one()
            assert tuple(role_state) == (
                False, False, False, True, False, False, True, False,
                True, True, True, False, True, True, False, False,
            )

        # Each denied DDL attempt gets an independent transaction so a prior
        # PostgreSQL error cannot make a later assertion pass accidentally.
        for statement in (
            "CREATE ROLE agentguard_test_forbidden",
            "CREATE TABLE agentguard_runtime_create_forbidden (id integer)",
            "CREATE TEMP TABLE agentguard_runtime_temp_forbidden (id integer)",
            "DROP TABLE agentguard_runtime_privilege_probe",
        ):
            session = factory()
            try:
                with pytest.raises(DBAPIError):
                    session.execute(text(statement))
            finally:
                session.rollback()
                session.close()

        original_metadata = events.events[1].metadata
        original_metadata["nested"] = [{"label": "password=after-construction-secret"},
                                       {"ordinary": "useful metadata"}]
        persist_execution_trace(factory, state, events)
        persist_execution_trace(factory, state, events)
        trace = retrieve_execution_trace(factory, state.execution_id)
        assert trace["execution"]["execution_status"] == state.execution_status
        assert trace["execution"]["completed_at"].utcoffset().total_seconds() == 0
        assert len(trace["events"]) == len(events.events)
        stored_metadata = trace["events"][1]["metadata"]
        assert "after-construction-secret" not in str(stored_metadata)
        assert stored_metadata["nested"][0]["label"] == "password=[REDACTED]"
        assert stored_metadata["nested"][1]["ordinary"] == "useful metadata"
        assert original_metadata["nested"][0]["label"] == "password=after-construction-secret"
        assert [item["event_index"] for item in trace["events"]] == list(range(len(events.events)))

        # Exact duplicate IDs in one input batch collapse to a single stored event.
        duplicate_state, duplicate_events = run_plan("duplicate batch candidate")
        persisted_ids.append(duplicate_state.execution_id)
        duplicate_events.events.append(duplicate_events.events[1].model_copy(deep=True))
        persist_execution_trace(factory, duplicate_state, duplicate_events)
        duplicate_trace = retrieve_execution_trace(factory, duplicate_state.execution_id)
        assert len(duplicate_trace["events"]) == len(duplicate_events.events) - 1
        assert [row["event_index"] for row in duplicate_trace["events"]] == list(range(len(duplicate_trace["events"])))

        # Conflicting duplicate IDs inside one batch roll back the execution upsert.
        batch_conflict_state = duplicate_state.model_copy(update={"execution_status": "failed"})
        batch_conflict_events = EventCollector(duplicate_state.execution_id, duplicate_state.task_id)
        batch_conflict_events.events = list(duplicate_events.events[:-1])
        batch_conflict_events.events.append(duplicate_events.events[1].model_copy(update={"status": "conflict"}))
        with pytest.raises(TelemetryPersistenceError, match="repeats with different content"):
            persist_execution_trace(factory, batch_conflict_state, batch_conflict_events)
        assert retrieve_execution_trace(factory, duplicate_state.execution_id)["execution"]["execution_status"] == duplicate_state.execution_status

        # Reusing an event ID with altered content must fail and roll back the
        # preceding execution upsert in the same transaction.
        changed_state = state.model_copy(update={"execution_status": "failed"})
        changed_event_id = EventCollector(state.execution_id, state.task_id)
        for index, event in enumerate(events.events):
            changed_event_id.emit(event.model_copy(update={"status": "conflicting"}) if index == 0 else event)
        with pytest.raises(TelemetryPersistenceError, match="event_id .* different content"):
            persist_execution_trace(factory, changed_state, changed_event_id)
        assert retrieve_execution_trace(factory, state.execution_id)["execution"]["execution_status"] == state.execution_status

        # A distinct event ID cannot take an already occupied event order.
        changed_order = EventCollector(state.execution_id, state.task_id)
        for index, event in enumerate(events.events):
            changed_order.emit(event.model_copy(update={"event_id": str(uuid4())}) if index == 0 else event)
        with pytest.raises(TelemetryPersistenceError, match="event_index 0 already belongs"):
            persist_execution_trace(factory, state, changed_order)

        # Both failed and partially completed executions are persisted.
        failed_state, failed_events = run_plan("failed run", failure_mode="exception")
        partial_state, partial_events = run_plan("partial run", plan=[
            ("calculator", {"operation": "add", "a": 1, "b": 2}),
            ("company_data", {"name": "Unknown Sample Company"}),
        ])
        assert failed_state.execution_status == "failed"
        assert partial_state.execution_status == "partially_completed"
        for extra_state, extra_events in ((failed_state, failed_events), (partial_state, partial_events)):
            persisted_ids.append(extra_state.execution_id)
            persist_execution_trace(factory, extra_state, extra_events)
            assert retrieve_execution_trace(factory, extra_state.execution_id)["execution"]["execution_status"] == extra_state.execution_status

        bad_state, bad_events = run_plan("rollback candidate")
        persisted_ids.append(bad_state.execution_id)
        bad_events.events[1].metadata["unserializable"] = object()
        with pytest.raises(TelemetryPersistenceError, match="rolled back"):
            persist_execution_trace(factory, bad_state, bad_events)
        assert retrieve_execution_trace(factory, bad_state.execution_id) is None

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(ExecutionEventRecord(event_id=str(uuid4()), execution_id="missing-execution",
                    task_id="missing-task", event_index=0, step_number=0,
                    timestamp=state.start_time, event_type="tool_started", component="tool", status="running"))
                session.flush()
    finally:
        with admin_factory.begin() as session:
            session.execute(delete(ExecutionRecord).where(ExecutionRecord.execution_id.in_(persisted_ids)))
            session.execute(text("DROP TABLE IF EXISTS agentguard_runtime_privilege_probe"))
        engine.dispose()
        admin_engine.dispose()


def test_fastapi_execution_persists_and_retrieves_with_runtime_role(monkeypatch):
    """Exercise API write/read wiring against the configured disposable DB."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.engine import make_url

    from agentguard.api import ApiSettings, create_app
    from agentguard.database.models import ExecutionEventRecord, ExecutionRecord

    monkeypatch.setenv("AGENTGUARD_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("AGENTGUARD_MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.setenv("AGENTGUARD_STORE_TASK_TEXT", "false")
    alembic = Config("alembic.ini")
    alembic.set_main_option("sqlalchemy.url", MIGRATION_URL.replace("%", "%%"))
    command.upgrade(alembic, "head")

    admin_engine = create_engine(MIGRATION_URL, pool_pre_ping=True, hide_parameters=True)
    admin_factory = sessionmaker(bind=admin_engine, expire_on_commit=False)
    runtime_role = make_url(TEST_URL).username
    quoted_role = admin_engine.dialect.identifier_preparer.quote(runtime_role)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
        connection.exec_driver_sql(f"GRANT SELECT, INSERT, UPDATE ON TABLE executions TO {quoted_role}")
        connection.exec_driver_sql(f"GRANT SELECT, INSERT ON TABLE execution_events TO {quoted_role}")

    app = create_app(ApiSettings(environment="test", docs_enabled=False))
    execution_id = None
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/executions",
                json={"task": "Calculate 2 plus 3", "mode": "deterministic"},
            )
            assert created.status_code == 201
            assert created.json()["persisted"] is True
            execution_id = created.json()["execution_id"]

            summary = client.get(f"/api/v1/executions/{execution_id}")
            assert summary.status_code == 200
            assert summary.json()["execution_id"] == execution_id
            assert summary.json()["status"] == "completed"

            page = client.get(
                f"/api/v1/executions/{execution_id}/events",
                params={"limit": 2},
            )
            assert page.status_code == 200
            first_page = page.json()
            assert len(first_page["events"]) == 2
            assert [event["event_index"] for event in first_page["events"]] == [0, 1]
            assert first_page["next_after_event_index"] == 1

            next_page = client.get(
                f"/api/v1/executions/{execution_id}/events",
                params={"limit": 10, "after_event_index": first_page["next_after_event_index"]},
            )
            assert next_page.status_code == 200
            assert [event["event_index"] for event in next_page.json()["events"]] == list(
                range(2, len(next_page.json()["events"]) + 2)
            )
            assert next_page.json()["next_after_event_index"] is None
    finally:
        if execution_id is not None:
            with admin_factory.begin() as session:
                session.execute(
                    delete(ExecutionEventRecord).where(
                        ExecutionEventRecord.execution_id == execution_id
                    )
                )
                session.execute(
                    delete(ExecutionRecord).where(ExecutionRecord.execution_id == execution_id)
                )
        admin_engine.dispose()
