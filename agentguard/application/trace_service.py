"""Trace retrieval for persisted executions and events."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from agentguard.application.errors import (
    ExecutionNotFoundError,
    PersistenceNotConfiguredError,
    PersistenceUnavailableError,
)
from agentguard.application.protocols import TraceReader
from agentguard.database.repositories import (
    retrieve_execution_events,
    retrieve_execution_trace,
)
from agentguard.telemetry.privacy import redact_metadata, redact_text

if TYPE_CHECKING:
    from agentguard.api.schemas.execution import (
        ExecutionEventsResponse,
        ExecutionSummaryResponse,
    )

LOGGER = logging.getLogger("agentguard.api.trace")


class TraceService:
    def __init__(self, reader: TraceReader, *, persistence_enabled: bool) -> None:
        self.reader = reader
        self.persistence_enabled = persistence_enabled

    def get_execution(self, execution_id: str) -> ExecutionSummaryResponse:
        from agentguard.api.schemas.execution import ExecutionSummaryResponse

        if not self.persistence_enabled:
            raise PersistenceNotConfiguredError()
        trace = self.reader.get_execution(execution_id)
        if trace is None:
            raise ExecutionNotFoundError()
        execution = trace["execution"]
        metadata = execution.get("metadata") or {}
        duration_ms = execution.get("total_duration_ms")
        return ExecutionSummaryResponse(
            execution_id=execution["execution_id"],
            task_id=execution["task_id"],
            task_description=redact_text(execution.get("task_description")),
            status=execution["execution_status"],
            mode=execution["execution_mode"],
            started_at=execution["started_at"],
            completed_at=execution.get("completed_at"),
            duration_ms=duration_ms,
            successful_tool_call_count=int(
                execution.get("successful_tool_call_count") or 0
            ),
            failed_tool_call_count=int(execution.get("failed_tool_call_count") or 0),
            metadata=redact_metadata(metadata),
        )

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_event_index: int | None,
    ) -> ExecutionEventsResponse:
        from agentguard.api.schemas.execution import (
            ExecutionEventResponse,
            ExecutionEventsResponse,
        )

        if not self.persistence_enabled:
            raise PersistenceNotConfiguredError()
        page = self.reader.list_events(
            execution_id,
            limit=limit,
            after_event_index=after_event_index,
        )
        if page is None:
            raise ExecutionNotFoundError()
        events = [
            ExecutionEventResponse(
                event_id=event["event_id"],
                execution_id=event["execution_id"],
                task_id=event["task_id"],
                event_index=event["event_index"],
                step_number=event["step_number"],
                timestamp=event["timestamp"],
                event_type=event["event_type"],
                component=event["component"],
                tool_name=event.get("tool_name"),
                status=event["status"],
                duration_ms=event.get("duration_ms"),
                error_type=event.get("error_type"),
                error_message=redact_text(event.get("error_message")),
                metadata=redact_metadata(event.get("metadata") or {}),
            )
            for event in page["events"]
        ]
        return ExecutionEventsResponse(
            execution_id=execution_id,
            events=events,
            next_after_event_index=page.get("next_after_event_index"),
        )


class DatabaseTraceReader:
    """Read-only adapter over the existing PostgreSQL repository functions."""

    def __init__(self, session_factory: sessionmaker[Session] | None) -> None:
        self.session_factory = session_factory

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        factory = self._session_factory()
        try:
            return retrieve_execution_trace(factory, execution_id)
        except SQLAlchemyError as exc:
            LOGGER.error(
                "Execution trace read failed (%s)",
                type(exc).__name__,
                extra={"execution_id": execution_id},
            )
            raise PersistenceUnavailableError() from None

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_event_index: int | None,
    ) -> dict[str, Any] | None:
        factory = self._session_factory()
        try:
            return retrieve_execution_events(
                factory,
                execution_id,
                limit=limit,
                after_event_index=after_event_index,
            )
        except SQLAlchemyError as exc:
            LOGGER.error(
                "Execution event read failed (%s)",
                type(exc).__name__,
                extra={"execution_id": execution_id},
            )
            raise PersistenceUnavailableError() from None

    def _session_factory(self) -> sessionmaker[Session]:
        if self.session_factory is None:
            raise PersistenceNotConfiguredError()
        return self.session_factory
