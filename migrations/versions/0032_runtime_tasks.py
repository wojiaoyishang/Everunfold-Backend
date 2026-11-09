"""Persistent scheduler plans and execution records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_runtime_tasks"
down_revision = "0031_draft_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_task_plans",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "runtime_task_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.String(80)),
        sa.UniqueConstraint("task_id", "planned_at"),
    )
    for column in ("task_id", "planned_at", "status"):
        op.create_index("ix_runtime_task_runs_" + column, "runtime_task_runs", [column])


def downgrade() -> None:
    op.drop_table("runtime_task_runs")
    op.drop_table("runtime_task_plans")
