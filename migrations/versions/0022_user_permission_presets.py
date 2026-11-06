"""Editable permission templates and non-authoritative user identity snapshots."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022_user_permission_presets"
down_revision = "0021_private_user_avatars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    presets = op.create_table(
        "user_permission_presets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(300), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("grants", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision >= 1", name="positive_revision"),
    )
    op.create_index(
        "uq_user_permission_presets_live_name",
        "user_permission_presets",
        ["name_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.add_column("users", sa.Column("permission_preset", JSONB()))
    now = datetime.now(UTC)
    op.bulk_insert(
        presets,
        [
            {
                "id": uuid5(NAMESPACE_URL, "everunfold:user-presets:" + code),
                "name": name,
                "name_key": name.casefold(),
                "description": description,
                "grants": {"everunfold": grants},
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            for code, name, description, grants in (
                (
                    "editor",
                    "编辑",
                    "编辑全部内容并使用媒体库，不直接发布。",
                    {
                        "content:read": "all",
                        "content:write": "all",
                        "media:read": "all",
                        "media:upload": "all",
                    },
                ),
                (
                    "reviewer",
                    "审稿人",
                    "查看、修改并审核发布全部内容。",
                    {
                        "content:read": "all",
                        "content:write": "all",
                        "content:publish": "all",
                        "media:read": "all",
                    },
                ),
            )
        ],
    )


def downgrade() -> None:
    raise RuntimeError("Review preset snapshots and audit history before a destructive downgrade")
