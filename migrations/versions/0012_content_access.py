"""Password access, timed public projection and hierarchical native categories."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_content_access"
down_revision = "0011_editor_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("content_drafts", "contents"):
        op.add_column(table, sa.Column("access_password_hash", sa.Text(), nullable=True))
    op.add_column("contents", sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "contents", sa.Column("open_synced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_contents_opens_at", "contents", ["opens_at"])
    op.add_column(
        "taxonomies", sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key("fk_taxonomy_parent", "taxonomies", "taxonomies", ["parent_id"], ["id"])


def downgrade() -> None:
    raise RuntimeError("Access configuration and taxonomy history must be preserved")
