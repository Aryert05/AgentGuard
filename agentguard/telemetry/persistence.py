"""Lifecycle adapter that persists completed executions only when configured."""
from __future__ import annotations

from agentguard.config.database import DatabaseSettings
from agentguard.database.connection import create_session_factory
from agentguard.database.repositories import persist_execution_trace
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
