"""Associate new comments with their account and seed reply notification rules."""

import sqlalchemy as sa
from alembic import op

revision = "0056_comment_reply_notices"
down_revision = "0055_notification_subjects"
branch_labels = None
depends_on = None

FK = "fk_comments_author_user_id_users"
INDEX = "ix_comments_author_user_id"


def upgrade() -> None:
    op.add_column("comments", sa.Column("author_user_id", sa.UUID()))
    op.create_foreign_key(
        op.f(FK), "comments", "users", ["author_user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(op.f(INDEX), "comments", ["author_user_id"])
    op.add_column(
        "users",
        sa.Column("comment_reply_email", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE notification_revisions SET revision = revision + 1 WHERE id = 1")
    op.execute(
        """
        INSERT INTO notification_routes
            (id, event_type, recipient_type, recipient_value, platform_id, revision, updated_at)
        SELECT seed.id::uuid, 'comment.reply_published', 'subject', 'subject',
               seed.platform, revisions.revision, now()
        FROM notification_revisions AS revisions
        CROSS JOIN (VALUES
            ('00000000-0000-4000-8000-000000000104', 'in-app'),
            ('00000000-0000-4000-8000-000000000105', 'email')
        ) AS seed(id, platform)
        WHERE revisions.id = 1
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_routes WHERE event_type = 'comment.reply_published'")
    op.drop_column("users", "comment_reply_email")
    op.drop_index(op.f(INDEX), table_name="comments")
    op.drop_constraint(op.f(FK), "comments", type_="foreignkey")
    op.drop_column("comments", "author_user_id")
