"""SQLAlchemy 2.x normalized execution and event tables."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (CheckConstraint, DateTime, Float, ForeignKeyConstraint, Index, Integer,
                        JSON, String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


json_type = JSON().with_variant(JSONB, "postgresql")


class ExecutionRecord(Base):
    __tablename__ = "executions"
    __table_args__ = (CheckConstraint(
        "execution_status IN ('pending','running','completed','partially_completed','failed')",
        name="ck_executions_status"),
        UniqueConstraint("execution_id", "task_id", name="uq_executions_execution_task"),
        Index("ix_executions_task_id", "task_id"),
        Index("ix_executions_started_at", "started_at"))

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_description: Mapped[Optional[str]] = mapped_column(Text)
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    successful_tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", json_type, nullable=False, default=dict)

    events: Mapped[list["ExecutionEventRecord"]] = relationship(
        back_populates="execution", cascade="all, delete-orphan", order_by="ExecutionEventRecord.event_index")


class ExecutionEventRecord(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        ForeignKeyConstraint(["execution_id", "task_id"], ["executions.execution_id", "executions.task_id"],
            ondelete="CASCADE", name="fk_events_execution_task"),
        UniqueConstraint("execution_id", "event_index", name="uq_execution_event_order"),
        Index("ix_execution_events_execution_time", "execution_id", "timestamp", "event_index"),
        Index("ix_execution_events_type", "event_type"),
        Index("ix_execution_events_tool", "tool_name"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    component: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_name: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    error_type: Mapped[Optional[str]] = mapped_column(String(160))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", json_type, nullable=False, default=dict)

    execution: Mapped[ExecutionRecord] = relationship(back_populates="events")
