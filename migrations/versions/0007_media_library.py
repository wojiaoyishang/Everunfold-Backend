"""Reusable content identities and explicit, recoverable media removal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_media_library"
down_revision = "0006_media_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("icon_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "media_assets",
        sa.Column("purge_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("media_assets", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE media_assets SET icon_enabled = TRUE WHERE purpose = 'icon'")
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"])
    op.create_table(
        "media_upload_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_assets.id"),
            nullable=False,
        ),
        sa.Column("reused", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_media_upload_receipts_asset_id", "media_upload_receipts", ["asset_id"])


def downgrade() -> None:
    raise RuntimeError("Media upload receipts and removal history must be retained")
