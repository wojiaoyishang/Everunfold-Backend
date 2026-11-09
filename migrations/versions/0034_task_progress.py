"""Ordered private progress events, shared by task features."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_task_progress"
down_revision = "0033_mail_test"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_progress_events",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("resource", sa.String(16), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.String(8192), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("completed", sa.Float()),
        sa.Column("total", sa.Float()),
        sa.Column("unit", sa.String(40)),
    )
    op.create_index(
        "ix_task_progress_resource_sequence",
        "task_progress_events",
        ["resource", "resource_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_table("task_progress_events")
