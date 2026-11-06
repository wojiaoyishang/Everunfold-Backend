"""Disabled-by-default authored pages and deployment-owned frontend registrations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023_managed_pages"
down_revision = "0022_user_permission_presets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("implementation_id", sa.String(100), unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer()),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("published", JSONB()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision >= 1", name="positive_revision"),
        sa.CheckConstraint("source IN ('custom', 'registered')", name="valid_source"),
    )
    op.create_table(
        "frontend_page_registrations",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("page_id", sa.String(100), primary_key=True),
        sa.Column("definition", JSONB(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Review authored page sources and public releases before destructive rollback"
    )
