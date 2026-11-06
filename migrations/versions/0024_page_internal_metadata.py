"""Private independently versioned page metadata; excluded from public page DTOs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0024_page_internal_metadata"
down_revision = "0023_managed_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "managed_pages",
        sa.Column(
            "internal_metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "managed_pages",
        sa.Column("internal_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "nonnegative_internal_revision", "managed_pages", "internal_revision >= 0"
    )


def downgrade() -> None:
    raise RuntimeError("Review private page metadata before destructive rollback")
