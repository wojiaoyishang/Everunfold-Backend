"""Scoped user keys, independently authenticated applications and callback outbox."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_api_keys_and_applications"
down_revision = "0019_account_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_applications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("query_key_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("callback_key_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1 AND auth_version >= 1", name="versions"),
    )

    table = sa.table(
        "api_applications",
        sa.column("id"),
        sa.column("name"),
        sa.column("audience"),
        sa.column("scopes", postgresql.JSONB()),
        sa.column("created_at"),
        sa.column("updated_at"),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "id": "everunfold",
                "name": "Everunfold",
                "audience": "everunfold",
                "scopes": [],
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("api_applications.id"), nullable=False),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("user_key_epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision >= 1 AND user_key_epoch >= 1", name="versions"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_app_id", "api_keys", ["app_id"])
    op.create_table(
        "auth_callback_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("app_id", sa.String(64), sa.ForeignKey("api_applications.id"), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(80), nullable=False, server_default=""),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'skipped')", name="status"
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
    )
    op.create_index("ix_auth_callback_events_app_id", "auth_callback_events", ["app_id"])
    op.create_index(
        "ix_auth_callback_events_available_at", "auth_callback_events", ["available_at"]
    )


def downgrade() -> None:
    raise RuntimeError(
        "Credential/audit history is durable; restore a reviewed backup to roll back"
    )
