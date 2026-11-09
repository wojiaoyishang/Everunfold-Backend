"""Unify moderated comments across articles and managed pages, preserving admission receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0029_comments"
down_revision = "0028_captcha"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("comments", "content_id", nullable=True)
    op.add_column(
        "comments",
        sa.Column(
            "page_id", UUID(as_uuid=True), sa.ForeignKey("managed_pages.id", ondelete="CASCADE")
        ),
    )
    op.create_index("ix_comments_page_id", "comments", ["page_id"])
    op.add_column(
        "comments", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "comments",
        sa.Column("previous_status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("comments", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.drop_constraint(op.f("ck_comments_ck_comments_status"), "comments", type_="check")
    op.create_check_constraint(
        "status",
        "comments",
        "status IN ('approved', 'pending', 'hidden', 'rejected', 'spam', 'trash')",
    )
    op.create_check_constraint("target", "comments", "(content_id IS NULL) <> (page_id IS NULL)")
    op.create_check_constraint("revision", "comments", "revision >= 1")
    op.execute("""
        INSERT INTO comments
            (id, content_id, page_id, author, content, created_at,
             status, revision, previous_status)
        SELECT s.id, c.id, CASE WHEN c.id IS NULL THEN p.id ELSE NULL END,
               s.data->>'author', s.data->>'body', s.created_at, 'pending', 1, 'pending'
        FROM reader_comment_submissions s
        LEFT JOIN contents c ON c.id = s.content_id
        LEFT JOIN managed_pages p ON p.id = s.content_id
        WHERE (c.id IS NOT NULL OR p.id IS NOT NULL)
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute("""
        UPDATE comments c SET parent_id = parent.id
        FROM reader_comment_submissions s, comments parent
        WHERE c.id = s.id AND s.data->>'parent_id' = parent.id::text
          AND (c.content_id = parent.content_id OR c.page_id = parent.page_id)
    """)


def downgrade() -> None:
    # Never silently discard page comments or moderation decisions.
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM comments WHERE page_id IS NOT NULL "
            "OR status IN ('rejected','spam','trash'))"
        )
    ):
        raise RuntimeError("Export and resolve page comments/moderation states before downgrade")
    op.drop_constraint(op.f("ck_comments_target"), "comments", type_="check")
    op.drop_constraint(op.f("ck_comments_revision"), "comments", type_="check")
    op.drop_constraint(op.f("ck_comments_status"), "comments", type_="check")
    op.create_check_constraint("status", "comments", "status IN ('approved', 'pending', 'hidden')")
    op.drop_index("ix_comments_page_id", "comments")
    for name in ("page_id", "revision", "previous_status", "updated_at"):
        op.drop_column("comments", name)
    op.alter_column("comments", "content_id", nullable=False)
