"""Store short-lived password recovery challenges and their delivery claims."""

import sqlalchemy as sa
from alembic import op

revision = "0052_password_recovery"
down_revision = "0051_invitation_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_recoveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("user_revision", sa.Integer(), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("policy_epoch", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("mail_profile_id", sa.String(36), nullable=False),
        sa.Column("mail_revision", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("delivery_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
    )
    for column in ("session_hash", "status", "expires_at"):
        op.create_index(f"ix_password_recoveries_{column}", "password_recoveries", [column])


def downgrade() -> None:
    op.drop_table("password_recoveries")
