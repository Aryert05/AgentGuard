"""PostgreSQL persistence and retrieval; transaction ownership is explicit."""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from agentguard.database.models import ExecutionEventRecord, ExecutionRecord
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState
from agentguard.telemetry.privacy import redact_metadata, safe_task_description

LOGGER = logging.getLogger("agentguard.telemetry")


class TelemetryPersistenceError(RuntimeError):
    """Raised when a complete telemetry transaction could not be committed."""


def persist_execution_trace(session_factory: sessionmaker[Session], state: ExecutionState,
                            collector: EventCollector, *, store_task_text: bool = False) -> None:
    """Atomically upsert the execution and insert its events idempotently."""
    if collector.execution_id != state.execution_id or collector.task_id != state.task_id:
        raise TelemetryPersistenceError("Execution and event collector identifiers do not match")
    for name, value in (("start_time", state.start_time), ("end_time", state.end_time)):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise TelemetryPersistenceError(f"Cannot persist a naive execution {name}")
    for event in collector.events:
        if event.execution_id != state.execution_id or event.task_id != state.task_id:
            raise TelemetryPersistenceError("An event does not belong to the execution being persisted")
        if event.timestamp.tzinfo is None or event.timestamp.utcoffset() is None:
            raise TelemetryPersistenceError("Cannot persist a naive event timestamp")
    try:
        with session_factory.begin() as session:
            _upsert_execution(session, state, store_task_text=store_task_text)
            _insert_events(session, collector.events)
    except TelemetryPersistenceError:
        raise
    except Exception as exc:
        # Avoid exception text: DBAPI exceptions may embed bound parameters or URLs.
        LOGGER.error("Telemetry persistence transaction failed (%s)", type(exc).__name__,
                     extra={"execution_id": state.execution_id})
        raise TelemetryPersistenceError(
            f"Could not persist execution {state.execution_id}; transaction rolled back"
        ) from None


def _upsert_execution(session: Session, state: ExecutionState, *, store_task_text: bool) -> None:
    duration = None
    if state.end_time:
        duration = max(0.0, (state.end_time - state.start_time).total_seconds() * 1000)
    successes = sum(call.status == "completed" for call in state.tool_calls)
    failures = sum(call.status == "failed" for call in state.tool_calls)
    values = {"execution_id": state.execution_id, "task_id": state.task_id,
        "task_description": safe_task_description(state.user_input) if store_task_text else None,
        "execution_status": state.execution_status, "execution_mode": state.execution_mode,
        "started_at": state.start_time.astimezone(timezone.utc),
        "completed_at": state.end_time.astimezone(timezone.utc) if state.end_time else None,
        "total_duration_ms": duration, "successful_tool_call_count": successes,
        "failed_tool_call_count": failures,
        "metadata": {"failure_mode": state.failure_mode, "current_step": state.current_step}}
    table = ExecutionRecord.__table__
    statement = pg_insert(table).values(**values).on_conflict_do_update(
        index_elements=[table.c.execution_id],
        set_={key: value for key, value in values.items() if key != "execution_id"})
    session.execute(statement)


def _insert_events(session: Session, events: list[Any]) -> None:
    if not events:
        return
    values = []
    by_event_id: dict[str, dict[str, Any]] = {}
    for event in events:
        row = {"event_id": event.event_id, "execution_id": event.execution_id,
            "task_id": event.task_id, "step_number": event.step_number,
            "timestamp": event.timestamp.astimezone(timezone.utc),
            "event_type": event.event_type.value, "component": event.component,
            "tool_name": event.tool_name, "status": event.status,
            "duration_ms": event.duration_ms, "error_type": event.error_type,
            "error_message": event.error_message, "metadata": redact_metadata(event.metadata)}
        previous = by_event_id.get(event.event_id)
        if previous is not None:
            previous_content = {key: value for key, value in previous.items() if key != "event_index"}
            current_content = {key: value for key, value in row.items() if key != "event_index"}
            if previous_content != current_content:
                raise TelemetryPersistenceError(
                    f"Conflicting telemetry event: event_id {event.event_id} repeats with different content in batch"
                )
            continue
        by_event_id[event.event_id] = row
        row["event_index"] = len(values)
        values.append(row)
    table = ExecutionEventRecord.__table__
    inserted_ids = set(session.scalars(
        pg_insert(table).values(values).on_conflict_do_nothing().returning(table.c.event_id)
    ).all())
    skipped = [row for row in values if row["event_id"] not in inserted_ids]
    if not skipped:
        return

    # A conflict may be an exact retry (same event ID and event index), an
    # event ID reused for different content, or an event index reused by a
    # different event. Resolve both uniqueness keys and compare every field.
    predicates = []
    for row in skipped:
        predicates.append(ExecutionEventRecord.event_id == row["event_id"])
        predicates.append((ExecutionEventRecord.execution_id == row["execution_id"])
                          & (ExecutionEventRecord.event_index == row["event_index"]))
    existing = session.scalars(select(ExecutionEventRecord).where(or_(*predicates))).all()
    by_id = {event.event_id: event for event in existing}
    by_order = {(event.execution_id, event.event_index): event for event in existing}
    for row in skipped:
        conflict = by_id.get(row["event_id"]) or by_order.get((row["execution_id"], row["event_index"]))
        if conflict is None:
            raise TelemetryPersistenceError(
                f"Event insert for {row['event_id']} conflicted, but the conflicting record could not be read"
            )
        stored = {key: getattr(conflict, "metadata_json" if key == "metadata" else key) for key in row}
        if stored != row:
            if conflict.event_id == row["event_id"]:
                detail = f"event_id {row['event_id']} already exists with different content"
            else:
                detail = (f"execution {row['execution_id']} event_index {row['event_index']} "
                          f"already belongs to event_id {conflict.event_id}")
            raise TelemetryPersistenceError(f"Conflicting telemetry event: {detail}")


def retrieve_execution_trace(session_factory: sessionmaker[Session], execution_id: str) -> dict[str, Any] | None:
    """Return one execution and its ordered event dictionaries."""
    with session_factory() as session:
        execution = session.get(ExecutionRecord, execution_id)
        if execution is None:
            return None
        events = session.scalars(select(ExecutionEventRecord)
            .where(ExecutionEventRecord.execution_id == execution_id)
            .order_by(ExecutionEventRecord.event_index)).all()
        return {"execution": {"execution_id": execution.execution_id, "task_id": execution.task_id,
                "task_description": execution.task_description, "execution_status": execution.execution_status,
                "execution_mode": execution.execution_mode, "started_at": execution.started_at,
                "completed_at": execution.completed_at, "total_duration_ms": execution.total_duration_ms,
                "successful_tool_call_count": execution.successful_tool_call_count,
                "failed_tool_call_count": execution.failed_tool_call_count, "metadata": execution.metadata_json},
            "events": [{"event_id": event.event_id, "execution_id": event.execution_id,
                "task_id": event.task_id, "event_index": event.event_index, "step_number": event.step_number,
                "timestamp": event.timestamp, "event_type": event.event_type,
                "component": event.component, "tool_name": event.tool_name, "status": event.status,
                "duration_ms": event.duration_ms, "error_type": event.error_type,
                "error_message": event.error_message, "metadata": event.metadata_json} for event in events]}
