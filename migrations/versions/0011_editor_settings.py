"""Draft and published editor properties, preserving every existing content row."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_editor_settings"
down_revision = "0010_content_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("content_drafts", "contents"):
        op.add_column(
            table,
            sa.Column("editor_settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    raise RuntimeError("Content settings and retained revisions must be preserved")
