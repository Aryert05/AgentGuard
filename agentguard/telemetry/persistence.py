"""Lifecycle adapter that persists completed executions only when configured."""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from agentguard.config.database import DatabaseSettings
from agentguard.database.connection import create_session_factory
from agentguard.database.repositories import persist_execution_trace
from agentguard.diagnostics.types import RecoverySession
from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState


def persist_if_configured(state: ExecutionState, events: EventCollector,
                          settings: DatabaseSettings) -> bool:
    """Persist a trace atomically, or return False without connecting when disabled."""
    if not settings.enabled:
        return False
    factory = create_session_factory(settings)
    try:
        persist_execution_trace(factory, state, events, store_task_text=settings.store_task_text)
    finally:
        factory.kw["bind"].dispose()
    return True


class DatabaseRecoveryTelemetry:
    """Persist completed recovery sessions through the existing repository boundary."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record(self, session: RecoverySession) -> None:
        from agentguard.database.repositories import persist_recovery_session

        persist_recovery_session(self.session_factory, session)

    def claim(self, execution_id: str, maximum: int) -> bool:
        """Atomically enforce a per-execution cap across processes and restarts."""
        from agentguard.database.repositories import claim_recovery_attempt_budget

        return claim_recovery_attempt_budget(self.session_factory, execution_id, maximum)
