"""Public execution request and response contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentguard.agent.graph import MAX_TOOL_CALLS

ExecutionMode = Literal["deterministic", "llm"]
ExecutionStatus = Literal[
    "pending",
    "running",
    "completed",
    "partially_completed",
    "failed",
]
ToolCallStatus = Literal["started", "completed", "failed"]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=8000)
    mode: ExecutionMode = "deterministic"
    max_tool_calls: int = Field(default=MAX_TOOL_CALLS, ge=1, le=MAX_TOOL_CALLS)

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Task must not be blank")
        return value


class ToolCallSummary(BaseModel):
    tool_name: str
    status: ToolCallStatus
    duration_ms: float | None = None


class ExecutionResponse(BaseModel):
    execution_id: str
    task_id: str
    status: ExecutionStatus
    mode: ExecutionMode
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    successful_tool_call_count: int
    failed_tool_call_count: int
    tool_calls: list[ToolCallSummary]
    final_output: str | None
    persisted: bool


class ExecutionSummaryResponse(BaseModel):
    execution_id: str
    task_id: str
    task_description: str | None
    status: ExecutionStatus
    mode: ExecutionMode
    started_at: datetime
    completed_at: datetime | None
    duration_ms: float | None
    successful_tool_call_count: int
    failed_tool_call_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEventResponse(BaseModel):
    event_id: str
    execution_id: str
    task_id: str
    event_index: int
    step_number: int
    timestamp: datetime
    event_type: str
    component: str
    tool_name: str | None
    status: str
    duration_ms: float | None
    error_type: str | None
    error_message: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEventsResponse(BaseModel):
    execution_id: str
    events: list[ExecutionEventResponse]
    next_after_event_index: int | None
