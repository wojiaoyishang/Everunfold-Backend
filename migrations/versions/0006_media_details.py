"""Media metadata and immutable edited-copy lineage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_media_details"
down_revision = "0005_icon_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, kind in (
        ("title", sa.String(160)),
        ("alt_text", sa.String(1000)),
        ("caption", sa.Text()),
        ("description", sa.Text()),
    ):
        op.add_column("media_assets", sa.Column(name, kind, nullable=False, server_default=""))
    op.add_column(
        "media_assets", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "media_assets",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_assets.id")),
    )
    op.add_column("media_assets", sa.Column("edit_recipe", postgresql.JSONB()))


def downgrade() -> None:
    raise RuntimeError(
        "Preserve media metadata and edit lineage; destructive downgrade is unsupported"
    )
