"""Store draft and published component documents separately from block content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0061_component_documents"
down_revision = "0060_shortcode_regions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("content_drafts", "contents"):
        op.add_column(table, sa.Column("component_document", JSONB(none_as_null=True)))
    op.add_column(
        "content_drafts",
        sa.Column("edit_mode", sa.String(16), nullable=False, server_default="blocks"),
    )


def downgrade() -> None:
    op.drop_column("content_drafts", "edit_mode")
    for table in ("contents", "content_drafts"):
        op.drop_column(table, "component_document")
