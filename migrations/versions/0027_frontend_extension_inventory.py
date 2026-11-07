"""Deploy-owned plugin/theme catalogs; activation remains in reliable site settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0027_extensions"
down_revision = "0026_page_languages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frontend_extension_inventories",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("frontend_extension_inventories")
