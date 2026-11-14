"""Store the display format and optional website of a reader comment."""

import sqlalchemy as sa
from alembic import op

revision = "0062_discussion_comments"
down_revision = "0061_component_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "comments",
        sa.Column("body_format", sa.String(8), nullable=False, server_default="plain"),
    )
    op.add_column("comments", sa.Column("website", sa.String(300)))
    op.create_check_constraint(
        op.f("ck_comments_body_format"), "comments", "body_format IN ('plain', 'markdown')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_comments_body_format"), "comments", type_="check")
    op.drop_column("comments", "website")
    op.drop_column("comments", "body_format")
