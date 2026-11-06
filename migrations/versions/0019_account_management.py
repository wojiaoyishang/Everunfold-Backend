"""Private profiles, scoped grants and durable account retry/audit records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_account_management"
down_revision = "0018_content_languages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("revision", "auth_version", "key_epoch"):
        op.add_column("users", sa.Column(name, sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "users", sa.Column("grants", postgresql.JSONB(), nullable=False, server_default="{}")
    )
    for name, kind, default in (
        ("email", sa.String(254), ""),
        ("avatar_url", sa.Text(), ""),
        ("bio", sa.Text(), ""),
        ("website", sa.Text(), ""),
        ("preferred_locale", sa.String(32), "zh-CN"),
    ):
        op.add_column("users", sa.Column(name, kind, nullable=False, server_default=default))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_check_constraint("positive_versions", "users", "revision >= 1 AND auth_version >= 1")
    op.add_column(
        "auth_audit_events",
        sa.Column("target_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.add_column(
        "auth_audit_events",
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "account_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(255), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Account history is durable; restore a reviewed backup to roll back")
