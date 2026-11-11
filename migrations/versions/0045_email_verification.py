"""Add account email verification state and short-lived Worker requests."""

import sqlalchemy as sa
from alembic import op

revision = "0045_email_verification"
down_revision = "0044_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("delivery_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mail_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'failed', 'expired', 'verified')",
            name="verification_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_email_verifications"),
    )
    op.create_index(
        "ix_email_verifications_user_id",
        "email_verifications",
        ["user_id"],
    )
    op.create_index(
        "ix_email_verifications_status",
        "email_verifications",
        ["status"],
    )
    op.create_index(
        "ix_email_verifications_created_at",
        "email_verifications",
        ["created_at"],
    )
    op.create_index(
        "ix_email_verifications_expires_at",
        "email_verifications",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verifications_expires_at",
        table_name="email_verifications",
    )
    op.drop_index(
        "ix_email_verifications_created_at",
        table_name="email_verifications",
    )
    op.drop_index(
        "ix_email_verifications_status",
        table_name="email_verifications",
    )
    op.drop_index(
        "ix_email_verifications_user_id",
        table_name="email_verifications",
    )
    op.drop_table("email_verifications")
    op.drop_column("users", "email_verified_at")
