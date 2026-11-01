"""SQLAlchemy 2.x normalized execution and event tables."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKeyConstraint,
                        Index, Integer, JSON, String, Text, UniqueConstraint)
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


class RecoveryAttemptBudgetRecord(Base):
    __tablename__ = "recovery_attempt_budgets"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_recovery_attempt_budgets_nonnegative"),
    )

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoverySessionRecord(Base):
    __tablename__ = "recovery_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('recovered','exhausted','blocked','no_action')",
            name="ck_recovery_sessions_status"),
        CheckConstraint(
            "final_execution_outcome IN ('completed','recovered','partially_completed','failed','unknown')",
            name="ck_recovery_sessions_final_outcome"),
        Index("ix_recovery_sessions_original_execution", "original_execution_id"),
        Index("ix_recovery_sessions_started_at", "started_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    successful_recovery_execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    final_execution_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    diagnosis_category: Mapped[str] = mapped_column(String(40), nullable=False)
    is_failure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    terminal_observed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    partial_results_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    diagnosis_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False, default=dict)
    recommendations_json: Mapped[list[dict[str, Any]]] = mapped_column(json_type, nullable=False, default=list)
    blocked_reasons_json: Mapped[list[str]] = mapped_column(json_type, nullable=False, default=list)
    policy_json: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False, default=dict)
    ml_risk_json: Mapped[Optional[dict[str, Any]]] = mapped_column(json_type)
    telemetry_version: Mapped[str] = mapped_column(String(20), nullable=False)

    attempts: Mapped[list["RecoveryAttemptRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="RecoveryAttemptRecord.attempt_number")


class RecoveryAttemptRecord(Base):
    __tablename__ = "recovery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["session_id"], ["recovery_sessions.session_id"],
            ondelete="CASCADE", name="fk_recovery_attempts_session"),
        CheckConstraint(
            "status IN ('succeeded','failed')",
            name="ck_recovery_attempts_status"),
        CheckConstraint("attempt_number >= 1", name="ck_recovery_attempts_positive_number"),
        CheckConstraint("chain_depth = 1", name="ck_recovery_attempts_chain_depth_one"),
        CheckConstraint("completed_at >= started_at", name="ck_recovery_attempts_time_order"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_recovery_attempts_duration_nonnegative"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms <= "
            "(EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000.0 + 0.001)",
            name="ck_recovery_attempts_duration_within_timestamps",
        ),
        UniqueConstraint("session_id", "attempt_number", name="uq_recovery_attempts_session_number"),
        UniqueConstraint("recovery_execution_id", name="uq_recovery_attempts_recovery_execution"),
        Index("ix_recovery_attempts_session", "session_id"),
        Index("ix_recovery_attempts_original_execution", "original_execution_id"),
        Index("ix_recovery_attempts_triggering_event", "triggering_event_id"),
        Index("ix_recovery_attempts_started_at", "started_at"),
    )

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    original_execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    triggering_event_id: Mapped[Optional[str]] = mapped_column(String(36))
    diagnosis_category: Mapped[str] = mapped_column(String(40), nullable=False)
    recovery_execution_id: Mapped[Optional[str]] = mapped_column(String(36))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chain_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actual_action: Mapped[str] = mapped_column(String(40), nullable=False)
    target_tool: Mapped[Optional[str]] = mapped_column(String(120))
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    output: Mapped[Optional[str]] = mapped_column(Text)
    error_type: Mapped[Optional[str]] = mapped_column(String(160))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", json_type, nullable=False, default=dict)

    session: Mapped[RecoverySessionRecord] = relationship(back_populates="attempts")
