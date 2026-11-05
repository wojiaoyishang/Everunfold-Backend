"""Native custom shortcode drafts and immutable applied sources."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_native_shortcodes"
down_revision = "0015_rich_source_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shortcode_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("applied_revision", sa.Integer()),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "shortcode_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("definition_id", "revision"),
    )
    op.create_index("ix_shortcode_versions_definition_id", "shortcode_versions", ["definition_id"])


def downgrade() -> None:
    op.drop_table("shortcode_versions")
    op.drop_table("shortcode_definitions")
