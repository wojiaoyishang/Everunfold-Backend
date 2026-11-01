"""Separate record removal from shared original retention; durable group scopes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_media_safe_delete"
down_revision = "0007_media_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE media_assets SET removed_at = purged_at WHERE purged_at IS NOT NULL")
    op.create_table(
        "media_removal_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("delete_original", sa.Boolean(), nullable=False),
        sa.Column("safe_delete", sa.Boolean(), nullable=False),
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("record_ids", postgresql.JSONB(), nullable=False),
        sa.Column("file_ids", postgresql.JSONB(), nullable=False),
        sa.Column("file_retained", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_media_removal_receipts_sha256", "media_removal_receipts", ["sha256"])


def downgrade() -> None:
    raise RuntimeError("Removal receipts and retained original identities must be preserved")
