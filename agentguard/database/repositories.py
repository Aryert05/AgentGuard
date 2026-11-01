"""PostgreSQL persistence and retrieval; transaction ownership is explicit."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from agentguard.database.models import (
    ExecutionEventRecord,
    ExecutionRecord,
    RecoveryAttemptRecord,
    RecoveryAttemptBudgetRecord,
    RecoverySessionRecord,
)
from agentguard.diagnostics.types import RecoverySession
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState
from agentguard.telemetry.privacy import redact_metadata, redact_text, safe_task_description

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


def persist_recovery_session(session_factory: sessionmaker[Session], session: RecoverySession) -> None:
    """Atomically upsert a recovery session and insert its attempts idempotently."""
    for name, value in (("started_at", session.started_at), ("completed_at", session.completed_at)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TelemetryPersistenceError(f"Cannot persist a naive recovery session {name}")
    attempt_ids: set[str] = set()
    attempt_numbers: set[int] = set()
    recovery_execution_ids: set[str] = set()
    diagnosis_event_ids = {
        event_id
        for evidence in session.diagnosis.evidence
        for event_id in evidence.event_ids
        if event_id
    }
    for attempt in session.attempts:
        if attempt.session_id != session.session_id:
            raise TelemetryPersistenceError("A recovery attempt does not belong to the session being persisted")
        if attempt.attempt_id in attempt_ids or attempt.attempt_number in attempt_numbers:
            raise TelemetryPersistenceError("Recovery attempt IDs and numbers must be unique within a session")
        if attempt.recovery_execution_id and attempt.recovery_execution_id in recovery_execution_ids:
            raise TelemetryPersistenceError("Recovery execution IDs must be unique within a session")
        if attempt.started_at.tzinfo is None or attempt.started_at.utcoffset() is None:
            raise TelemetryPersistenceError("Cannot persist a naive recovery attempt timestamp")
        if attempt.completed_at.tzinfo is None or attempt.completed_at.utcoffset() is None:
            raise TelemetryPersistenceError("Cannot persist a naive recovery attempt timestamp")
        if attempt.completed_at < attempt.started_at:
            raise TelemetryPersistenceError(
                "Cannot persist a recovery attempt completed before it started"
            )
        if not isinstance(attempt.triggering_event_id, str) or not attempt.triggering_event_id.strip():
            raise TelemetryPersistenceError(
                "Cannot persist a recovery attempt without a triggering event ID"
            )
        if attempt.triggering_event_id not in diagnosis_event_ids:
            raise TelemetryPersistenceError(
                "Recovery attempt triggering event ID is not present in diagnosis evidence"
            )
        if attempt.duration_ms is not None:
            elapsed_ms = (attempt.completed_at - attempt.started_at).total_seconds() * 1000
            if (not math.isfinite(attempt.duration_ms) or attempt.duration_ms < 0
                    or attempt.duration_ms > elapsed_ms + 0.001):
                raise TelemetryPersistenceError(
                    "Recovery attempt duration is inconsistent with its timestamps"
                )
        attempt_ids.add(attempt.attempt_id)
        attempt_numbers.add(attempt.attempt_number)
        if attempt.recovery_execution_id:
            recovery_execution_ids.add(attempt.recovery_execution_id)
    try:
        with session_factory.begin() as session_scope:
            _upsert_recovery_session(session_scope, session)
            _insert_recovery_attempts(session_scope, session)
    except TelemetryPersistenceError:
        raise
    except Exception as exc:
        LOGGER.error("Recovery telemetry persistence transaction failed (%s)", type(exc).__name__,
                     extra={"recovery_session_id": session.session_id})
        raise TelemetryPersistenceError(
            f"Could not persist recovery session {session.session_id}; transaction rolled back"
        ) from None


def claim_recovery_attempt_budget(
    session_factory: sessionmaker[Session],
    execution_id: str,
    maximum: int,
) -> bool:
    """Atomically reserve one durable recovery attempt for an execution."""
    if not execution_id.strip() or maximum < 1:
        raise TelemetryPersistenceError("Recovery budget requires an execution ID and positive maximum")
    table = RecoveryAttemptBudgetRecord.__table__
    now = datetime.now(timezone.utc)
    statement = pg_insert(table).values(
        execution_id=execution_id,
        attempt_count=1,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=[table.c.execution_id],
        set_={
            "attempt_count": table.c.attempt_count + 1,
            "updated_at": now,
        },
        where=table.c.attempt_count < maximum,
    ).returning(table.c.attempt_count)
    try:
        with session_factory.begin() as session:
            return session.execute(statement).scalar_one_or_none() is not None
    except Exception as exc:
        LOGGER.error("Recovery budget transaction failed (%s)", type(exc).__name__,
                     extra={"execution_id": execution_id})
        raise TelemetryPersistenceError("Could not reserve a recovery attempt") from None


def _upsert_recovery_session(session: Session, recovery: RecoverySession) -> None:
    values = {
        "session_id": recovery.session_id,
        "original_execution_id": recovery.original_execution_id,
        "successful_recovery_execution_id": recovery.successful_recovery_execution_id,
        "status": recovery.status.value,
        "final_execution_outcome": recovery.final_execution_outcome.value,
        "diagnosis_category": recovery.diagnosis.primary_category.value,
        "is_failure": recovery.diagnosis.is_failure,
        "terminal_observed": recovery.diagnosis.terminal_observed,
        "partial_results_available": recovery.diagnosis.partial_results_available,
        "started_at": recovery.started_at.astimezone(timezone.utc),
        "completed_at": recovery.completed_at.astimezone(timezone.utc),
        "diagnosis_json": recovery.diagnosis.model_dump(mode="json"),
        "recommendations_json": [item.model_dump(mode="json") for item in recovery.recommendations],
        "blocked_reasons_json": list(recovery.blocked_reasons),
        "policy_json": recovery.policy.model_dump(mode="json"),
        "ml_risk_json": recovery.diagnosis.ml_risk.model_dump(mode="json") if recovery.diagnosis.ml_risk else None,
        "telemetry_version": recovery.telemetry_version,
    }
    table = RecoverySessionRecord.__table__
    statement = pg_insert(table).values(**values).on_conflict_do_update(
        index_elements=[table.c.session_id],
        set_={key: value for key, value in values.items() if key != "session_id"})
    session.execute(statement)


def _insert_recovery_attempts(session: Session, recovery: RecoverySession) -> None:
    if not recovery.attempts:
        return
    values: list[dict[str, Any]] = []
    by_attempt_id: dict[str, dict[str, Any]] = {}
    for attempt in recovery.attempts:
        row = {
            "attempt_id": attempt.attempt_id,
            "session_id": attempt.session_id,
            "original_execution_id": attempt.original_execution_id,
            "triggering_event_id": attempt.triggering_event_id,
            "diagnosis_category": attempt.diagnosis_category.value,
            "recovery_execution_id": attempt.recovery_execution_id,
            "attempt_number": attempt.attempt_number,
            "chain_depth": attempt.chain_depth,
            "action": attempt.action.value,
            "actual_action": attempt.actual_action.value,
            "target_tool": attempt.target_tool,
            "risk": attempt.risk.value,
            "status": attempt.status.value,
            "started_at": attempt.started_at.astimezone(timezone.utc),
            "completed_at": attempt.completed_at.astimezone(timezone.utc),
            "output": redact_text(attempt.output),
            "error_type": attempt.error_type,
            "error_message": redact_text(attempt.error_message),
            "duration_ms": attempt.duration_ms,
            "metadata": redact_metadata(attempt.metadata),
        }
        previous = by_attempt_id.get(attempt.attempt_id)
        if previous is not None:
            if previous != row:
                raise TelemetryPersistenceError(
                    f"Conflicting recovery attempt: attempt_id {attempt.attempt_id} repeats with different content"
                )
            continue
        by_attempt_id[attempt.attempt_id] = row
        values.append(row)
    table = RecoveryAttemptRecord.__table__
    inserted_ids = set(session.scalars(
        pg_insert(table).values(values).on_conflict_do_nothing().returning(table.c.attempt_id)
    ).all())
    skipped = [row for row in values if row["attempt_id"] not in inserted_ids]
    if not skipped:
        return
    predicates = []
    for row in skipped:
        predicates.append(RecoveryAttemptRecord.attempt_id == row["attempt_id"])
        predicates.append(
            (RecoveryAttemptRecord.session_id == row["session_id"])
            & (RecoveryAttemptRecord.attempt_number == row["attempt_number"])
        )
        if row["recovery_execution_id"]:
            predicates.append(
                RecoveryAttemptRecord.recovery_execution_id == row["recovery_execution_id"]
            )
    existing = session.scalars(select(RecoveryAttemptRecord).where(or_(*predicates))).all()
    by_id = {record.attempt_id: record for record in existing}
    by_number = {(record.session_id, record.attempt_number): record for record in existing}
    by_recovery_id = {
        record.recovery_execution_id: record for record in existing if record.recovery_execution_id
    }
    for row in skipped:
        conflict = (
            by_id.get(row["attempt_id"])
            or by_number.get((row["session_id"], row["attempt_number"]))
            or by_recovery_id.get(row["recovery_execution_id"])
        )
        if conflict is None:
            raise TelemetryPersistenceError(
                f"Recovery attempt insert for {row['attempt_id']} conflicted, but the existing record could not be read"
            )
        stored = {
            key: getattr(conflict, "metadata_json" if key == "metadata" else key)
            for key in row
        }
        if stored != row:
            raise TelemetryPersistenceError(
                f"Conflicting recovery attempt: {row['attempt_id']} or its ordering/recovery identity already exists with different content"
            )


def retrieve_recovery_session(session_factory: sessionmaker[Session], session_id: str) -> dict[str, Any] | None:
    """Return one recovery session and its ordered attempt records."""
    with session_factory() as session:
        record = session.get(RecoverySessionRecord, session_id)
        if record is None:
            return None
        attempts = session.scalars(
            select(RecoveryAttemptRecord)
            .where(RecoveryAttemptRecord.session_id == session_id)
            .order_by(RecoveryAttemptRecord.attempt_number)
        ).all()
        return {
            "session": {
                "session_id": record.session_id,
                "original_execution_id": record.original_execution_id,
                "successful_recovery_execution_id": record.successful_recovery_execution_id,
                "status": record.status,
                "final_execution_outcome": record.final_execution_outcome,
                "diagnosis_category": record.diagnosis_category,
                "is_failure": record.is_failure,
                "terminal_observed": record.terminal_observed,
                "partial_results_available": record.partial_results_available,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "diagnosis": record.diagnosis_json,
                "recommendations": record.recommendations_json,
                "blocked_reasons": record.blocked_reasons_json,
                "policy": record.policy_json,
                "ml_risk": record.ml_risk_json,
                "telemetry_version": record.telemetry_version,
            },
            "attempts": [
                {
                    "attempt_id": attempt.attempt_id,
                    "session_id": attempt.session_id,
                    "original_execution_id": attempt.original_execution_id,
                    "triggering_event_id": attempt.triggering_event_id,
                    "diagnosis_category": attempt.diagnosis_category,
                    "recovery_execution_id": attempt.recovery_execution_id,
                    "attempt_number": attempt.attempt_number,
                    "chain_depth": attempt.chain_depth,
                    "action": attempt.action,
                    "actual_action": attempt.actual_action,
                    "target_tool": attempt.target_tool,
                    "risk": attempt.risk,
                    "status": attempt.status,
                    "started_at": attempt.started_at,
                    "completed_at": attempt.completed_at,
                    "output": attempt.output,
                    "error_type": attempt.error_type,
                    "error_message": attempt.error_message,
                    "duration_ms": attempt.duration_ms,
                    "metadata": attempt.metadata_json,
                }
                for attempt in attempts
            ],
        }
