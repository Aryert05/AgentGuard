"""Agent execution state shared across graph nodes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    status: Literal["started", "completed", "failed"]
    result: Any = None
    error: Optional[str] = None


class ExecutionState(BaseModel):
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    user_input: str
    current_step: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    execution_status: Literal["pending", "running", "completed", "partially_completed", "failed"] = "pending"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    final_output: Optional[str] = None
    failure_mode: Optional[str] = None
