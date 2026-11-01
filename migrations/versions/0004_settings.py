"""Versioned settings, audit and delivery intents. Explicit migration only."""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_settings"
down_revision = "0003_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frontend_node_policies",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="revision"),
    )
    op.create_table(
        "settings_audit",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("changes", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "settings_delivery_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("source_epoch", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_id", pg.UUID(as_uuid=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(80)),
        sa.UniqueConstraint("node_id", "source_epoch", "resource", "revision"),
        sa.CheckConstraint("revision >= 1 AND attempts >= 0", name="version_attempts"),
    )
    op.create_index(
        "ix_settings_delivery_events_next_attempt_at",
        "settings_delivery_events",
        ["next_attempt_at"],
    )
    # An empty installation must have editable settings. Existing imported data is untouched.
    op.get_bind().execute(
        sa.text("""
        INSERT INTO site_settings (id, data, source_epoch, generation)
        VALUES (1, CAST(:data AS jsonb), gen_random_uuid(), 1) ON CONFLICT (id) DO NOTHING
    """),
        {
            "data": json.dumps(
                {
                    "name": "Everunfold",
                    "description": "",
                    "author": "Admin",
                    "avatar_url": None,
                    "hero_url": None,
                    "hero_title": "Everunfold",
                    "hero_subtitle": "",
                    "notice": "",
                    "navigation": [{"label": "首页", "href": "/", "icon": "home"}],
                }
            )
        },
    )


def downgrade() -> None:
    raise RuntimeError("Settings contain user data; automated destructive downgrade is disabled")
