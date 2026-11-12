"""Add encrypted TOTP credentials and one-time login challenges."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0049_two_factor"
down_revision = "0048_verified_email_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "two_factor_credentials",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column("setup_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_counter", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("recovery_hashes", JSONB(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "login_challenges",
        sa.Column(
            "session_hash",
            sa.String(64),
            sa.ForeignKey("browser_sessions.token_hash", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_login_challenges_user_id", "login_challenges", ["user_id"])
    op.create_index("ix_login_challenges_expires_at", "login_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("login_challenges")
    op.drop_table("two_factor_credentials")
