"""Create normalized execution and event telemetry tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_execution_telemetry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("executions",
        sa.Column("execution_id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("task_description", sa.Text(), nullable=True),
        sa.Column("execution_status", sa.String(32), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_duration_ms", sa.Float(), nullable=True),
        sa.Column("successful_tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("execution_status IN ('pending','running','completed','partially_completed','failed')", name="ck_executions_status"),
        sa.UniqueConstraint("execution_id", "task_id", name="uq_executions_execution_task"))
    op.create_index("ix_executions_task_id", "executions", ["task_id"])
    op.create_index("ix_executions_started_at", "executions", ["started_at"])
    op.create_table("execution_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error_type", sa.String(160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("execution_id", "event_index", name="uq_execution_event_order"),
        sa.ForeignKeyConstraint(["execution_id", "task_id"], ["executions.execution_id", "executions.task_id"],
            ondelete="CASCADE", name="fk_events_execution_task"))
    op.create_index("ix_execution_events_execution_time", "execution_events", ["execution_id", "timestamp", "event_index"])
    op.create_index("ix_execution_events_type", "execution_events", ["event_type"])
    op.create_index("ix_execution_events_tool", "execution_events", ["tool_name"])


def downgrade() -> None:
    op.drop_index("ix_execution_events_tool", table_name="execution_events")
    op.drop_index("ix_execution_events_type", table_name="execution_events")
    op.drop_index("ix_execution_events_execution_time", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_index("ix_executions_started_at", table_name="executions")
    op.drop_index("ix_executions_task_id", table_name="executions")
    op.drop_table("executions")
