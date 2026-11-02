"""Protocols separating HTTP routes from execution and trace implementations."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from agentguard.schemas.events import EventCollector
from agentguard.schemas.state import ExecutionState

ExecutionMode = Literal["deterministic", "llm"]


class ExecutionRunner(Protocol):
    def run(
        self,
        task: str,
        *,
        mode: ExecutionMode,
        max_tool_calls: int,
    ) -> tuple[ExecutionState, EventCollector]:
        """Run an agent and return its state and event collector."""


class TraceReader(Protocol):
    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Return a persisted execution summary or None."""

    def list_events(
        self,
        execution_id: str,
        *,
        limit: int,
        after_event_index: int | None,
    ) -> dict[str, Any] | None:
        """Return a bounded event page or None when the execution is unknown."""
