"""Icon media storage and resumable upload identity."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_icon_media"
down_revision = "0004_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("uploader_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_id", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("object_key"),
        sa.CheckConstraint("status IN ('staging', 'ready')", name="status"),
    )


def downgrade() -> None:
    raise RuntimeError("Preserve uploaded media records; destructive downgrade is not supported")
