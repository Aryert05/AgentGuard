"""Add bounded recovery session and attempt telemetry tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_recovery_telemetry"
down_revision = "0001_execution_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("recovery_attempt_budgets",
        sa.Column("execution_id", sa.String(36), primary_key=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_recovery_attempt_budgets_nonnegative"))
    op.create_table("recovery_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("original_execution_id", sa.String(36), nullable=True),
        sa.Column("successful_recovery_execution_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("final_execution_outcome", sa.String(32), nullable=False),
        sa.Column("diagnosis_category", sa.String(40), nullable=False),
        sa.Column("is_failure", sa.Boolean(), nullable=False),
        sa.Column("terminal_observed", sa.Boolean(), nullable=False),
        sa.Column("partial_results_available", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("diagnosis_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("recommendations_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("blocked_reasons_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ml_risk_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("telemetry_version", sa.String(20), nullable=False),
        sa.CheckConstraint("status IN ('recovered','exhausted','blocked','no_action')",
            name="ck_recovery_sessions_status"),
        sa.CheckConstraint(
            "final_execution_outcome IN ('completed','recovered','partially_completed','failed','unknown')",
            name="ck_recovery_sessions_final_outcome"))
    op.create_index("ix_recovery_sessions_original_execution", "recovery_sessions", ["original_execution_id"])
    op.create_index("ix_recovery_sessions_started_at", "recovery_sessions", ["started_at"])
    op.create_table("recovery_attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("original_execution_id", sa.String(36), nullable=True),
        sa.Column("triggering_event_id", sa.String(36), nullable=True),
        sa.Column("diagnosis_category", sa.String(40), nullable=False),
        sa.Column("recovery_execution_id", sa.String(36), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("chain_depth", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actual_action", sa.String(40), nullable=False),
        sa.Column("target_tool", sa.String(120), nullable=True),
        sa.Column("risk", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("status IN ('succeeded','failed')", name="ck_recovery_attempts_status"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_recovery_attempts_positive_number"),
        sa.CheckConstraint("chain_depth = 1", name="ck_recovery_attempts_chain_depth_one"),
        sa.CheckConstraint("completed_at >= started_at", name="ck_recovery_attempts_time_order"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0",
            name="ck_recovery_attempts_duration_nonnegative"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms <= "
            "(EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000.0 + 0.001)",
            name="ck_recovery_attempts_duration_within_timestamps"),
        sa.UniqueConstraint("session_id", "attempt_number", name="uq_recovery_attempts_session_number"),
        sa.UniqueConstraint("recovery_execution_id", name="uq_recovery_attempts_recovery_execution"),
        sa.ForeignKeyConstraint(["session_id"], ["recovery_sessions.session_id"],
            ondelete="CASCADE", name="fk_recovery_attempts_session"))
    op.create_index("ix_recovery_attempts_session", "recovery_attempts", ["session_id"])
    op.create_index("ix_recovery_attempts_original_execution", "recovery_attempts", ["original_execution_id"])
    op.create_index("ix_recovery_attempts_triggering_event", "recovery_attempts", ["triggering_event_id"])
    op.create_index("ix_recovery_attempts_started_at", "recovery_attempts", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_recovery_attempts_started_at", table_name="recovery_attempts")
    op.drop_index("ix_recovery_attempts_triggering_event", table_name="recovery_attempts")
    op.drop_index("ix_recovery_attempts_original_execution", table_name="recovery_attempts")
    op.drop_index("ix_recovery_attempts_session", table_name="recovery_attempts")
    op.drop_table("recovery_attempts")
    op.drop_index("ix_recovery_sessions_started_at", table_name="recovery_sessions")
    op.drop_index("ix_recovery_sessions_original_execution", table_name="recovery_sessions")
    op.drop_table("recovery_sessions")
    op.drop_table("recovery_attempt_budgets")
