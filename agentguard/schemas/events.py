"""Serializable execution telemetry contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from agentguard.telemetry.privacy import redact_metadata


class EventType(str, Enum):
    EXECUTION_STARTED = "execution_started"
    LLM_REQUEST = "llm_request"
    TOOL_SELECTED = "tool_selected"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    execution_id: str
    task_id: str
    step_number: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType
    component: str
    tool_name: Optional[str] = None
    status: str
    duration_ms: Optional[float] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Event timestamps must be timezone-aware")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def redact_sensitive_metadata(cls, value: Any) -> Any:
        return redact_metadata(value or {})

    @field_validator("error_message")
    @classmethod
    def redact_sensitive_error_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = re.sub(r"(?i)(api[_-]?key|access[_-]?token|authorization|password|secret|token)\s*[:=]\s*([^\s,;]+)", r"\1=[REDACTED]", value)
        cleaned = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", cleaned)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "[REDACTED]", cleaned)


class EventCollector:
    def __init__(self, execution_id: str | None = None, task_id: str | None = None) -> None:
        self.events: list[ExecutionEvent] = []
        self.execution_id = execution_id
        self.task_id = task_id
        self._event_ids: set[str] = set()
        self._last_timestamp: datetime | None = None
        self._terminal = False

    def emit(self, event: ExecutionEvent) -> None:
        if self._terminal:
            raise ValueError("Cannot append events after an execution terminal event")
        if self.execution_id is None:
            self.execution_id, self.task_id = event.execution_id, event.task_id
        if event.execution_id != self.execution_id or event.task_id != self.task_id:
            raise ValueError("Event execution_id/task_id do not match this collector")
        if event.event_id in self._event_ids:
            raise ValueError("Event IDs must be unique within an execution")
        if self._last_timestamp is not None and event.timestamp < self._last_timestamp:
            raise ValueError("Events must be emitted in timestamp order")
        if not self.events and event.event_type != EventType.EXECUTION_STARTED:
            raise ValueError("The first event must be execution_started")
        if self.events and event.event_type == EventType.EXECUTION_STARTED:
            raise ValueError("execution_started may only be emitted once")
        self._event_ids.add(event.event_id)
        self._last_timestamp = event.timestamp
        self.events.append(event)
        if event.event_type in (EventType.EXECUTION_COMPLETED, EventType.EXECUTION_FAILED):
            self._terminal = True

    def to_json(self, *, indent: int = 2) -> str:
        return "[" + ",\n".join(e.model_dump_json(indent=indent) for e in self.events) + "]"
