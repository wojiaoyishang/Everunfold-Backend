"""Retain existing page identities and allow any number of pages of one type."""

import sqlalchemy as sa
from alembic import op

revision = "0057_page_types"
down_revision = "0056_comment_reply_notices"
branch_labels = None
depends_on = None

TABLE = "managed_pages"


def upgrade() -> None:
    op.drop_constraint(op.f("uq_managed_pages_implementation_id"), TABLE, type_="unique")
    op.alter_column(TABLE, "implementation_id", new_column_name="type_id")
    op.execute("UPDATE managed_pages SET type_id = 'core.custom' WHERE source = 'custom'")
    op.alter_column(TABLE, "type_id", existing_type=sa.String(100), nullable=False)
    op.create_index(op.f("ix_managed_pages_type_id"), TABLE, ["type_id"])
    op.create_check_constraint(
        "type_source",
        TABLE,
        "(source = 'custom' AND type_id = 'core.custom') "
        "OR (source = 'registered' AND type_id <> 'core.custom')",
    )


def downgrade() -> None:
    # Restoring uniqueness refuses rollback if any registered type has multiple pages.
    op.drop_constraint(op.f("ck_managed_pages_type_source"), TABLE, type_="check")
    op.drop_index(op.f("ix_managed_pages_type_id"), table_name=TABLE)
    op.alter_column(TABLE, "type_id", existing_type=sa.String(100), nullable=True)
    op.execute("UPDATE managed_pages SET type_id = NULL WHERE source = 'custom'")
    op.alter_column(TABLE, "type_id", new_column_name="implementation_id")
    op.create_unique_constraint(
        op.f("uq_managed_pages_implementation_id"), TABLE, ["implementation_id"]
    )
