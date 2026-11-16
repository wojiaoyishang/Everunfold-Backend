"""Store article page views independently of publication revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0066_article_view_count"
down_revision = "0065_ai_writing_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contents", sa.Column("view_count", sa.BigInteger(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("contents", "view_count")
