"""Owned avatar library, bounded upload policy and stable selected-avatar references."""

import sqlalchemy as sa
from alembic import op

revision = "0021_private_user_avatars"
down_revision = "0020_api_keys_and_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_avatar_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_bytes", sa.Integer(), nullable=False, server_default="2097152"),
        sa.Column("max_edge", sa.Integer(), nullable=False, server_default="512"),
        sa.Column("daily_uploads", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("retained_images", sa.Integer(), nullable=False, server_default="20"),
    )
    op.execute("INSERT INTO account_avatar_policy (id) VALUES (1)")
    op.create_table(
        "account_avatars",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("media_assets.id")),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_account_avatars_user_id", "account_avatars", ["user_id"])
    op.create_index("ix_account_avatars_created_at", "account_avatars", ["created_at"])
    op.add_column("users", sa.Column("avatar_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_users_avatar_id_account_avatars", "users", "account_avatars", ["avatar_id"], ["id"]
    )


def downgrade() -> None:
    raise RuntimeError("Review backups and avatar ownership before a destructive downgrade")
