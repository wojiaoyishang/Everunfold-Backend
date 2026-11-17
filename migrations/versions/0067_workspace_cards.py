"""Persist workspace layouts and start anonymous daily view aggregates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0067_workspace_cards"
down_revision = "0066_article_view_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_layouts",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cards", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "article_view_days",
        sa.Column(
            "content_id",
            sa.Uuid(),
            sa.ForeignKey("contents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("views >= 0", name="ck_article_view_days_nonnegative_views"),
    )
    op.create_index("ix_article_view_days_day", "article_view_days", ["day"])
    op.create_table(
        "workspace_analytics_epoch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO workspace_analytics_epoch (id, started_at) VALUES (1, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("workspace_analytics_epoch")
    op.drop_table("article_view_days")
    op.drop_table("workspace_layouts")
