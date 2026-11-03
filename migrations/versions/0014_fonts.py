"""Validated fonts share the immutable media upload lifecycle."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_fonts"
down_revision = "0013_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("font_info", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "font_info")
