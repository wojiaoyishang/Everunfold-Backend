"""Add durable notification events, routes, templates, deliveries and inbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_notifications"
down_revision = "0043_glossary_image_viewer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1 AND revision >= 1", name="singleton_revision"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_revisions"),
    )
    op.execute("INSERT INTO notification_revisions (id, revision) VALUES (1, 1)")
    op.create_table(
        "notification_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("resource_type", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("resource_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("event_version >= 1", name="positive_event_version"),
        sa.CheckConstraint("jsonb_typeof(data) = 'object'", name="object_data"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_events"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_events_dedupe_key"),
    )
    op.create_index("ix_notification_events_event_type", "notification_events", ["event_type"])
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("platform_id", sa.String(length=120), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("html", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="positive_revision"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_templates"),
        sa.UniqueConstraint(
            "event_type",
            "platform_id",
            "locale",
            name="uq_notification_template_variant",
        ),
    )
    op.create_table(
        "notification_routes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("recipient_type", sa.String(length=16), nullable=False),
        sa.Column("recipient_value", sa.String(length=160), nullable=False),
        sa.Column("platform_id", sa.String(length=120), nullable=False),
        sa.Column("template_key", sa.String(length=120), nullable=False, server_default="default"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "recipient_type IN ('user', 'role', 'permission')",
            name="recipient_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_routes"),
        sa.UniqueConstraint(
            "event_type",
            "recipient_type",
            "recipient_value",
            "platform_id",
            name="uq_notification_route_target",
        ),
    )
    op.execute(
        """
        INSERT INTO notification_routes
            (id, event_type, recipient_type, recipient_value, platform_id,
             template_key, enabled, revision, updated_at)
        VALUES
            ('00000000-0000-4000-8000-000000000101',
             'comment.pending_review', 'permission', 'comments:moderate',
             'in-app', 'default', true, 1, now())
        """
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("platform_id", sa.String(length=120), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=True),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("html", sa.Text(), nullable=False, server_default=""),
        sa.Column("href", sa.Text(), nullable=False, server_default=""),
        sa.Column("destination", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.UUID(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'unknown', 'suppressed')",
            name="delivery_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        sa.ForeignKeyConstraint(["event_id"], ["notification_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["template_id"], ["notification_templates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.UniqueConstraint("event_id", "user_id", "platform_id", name="uq_notification_delivery"),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_table(
        "notification_inbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("href", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["notification_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_inbox"),
        sa.UniqueConstraint("event_id", "user_id", name="uq_notification_inbox"),
    )
    op.create_index(
        "ix_notification_inbox_user_created",
        "notification_inbox",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_inbox_user_created", table_name="notification_inbox")
    op.drop_table("notification_inbox")
    op.drop_index("ix_notification_deliveries_due", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_routes")
    op.drop_table("notification_templates")
    op.drop_index("ix_notification_events_event_type", table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_table("notification_revisions")
