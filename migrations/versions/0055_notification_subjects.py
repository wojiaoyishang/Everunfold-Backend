"""Lock notification targets and add administrator account notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0055_notification_subjects"
down_revision = "0054_cache_events"
branch_labels = None
depends_on = None

RECIPIENT_CHECK = "ck_notification_routes_recipient_type"
SUBJECT_FK = "fk_notification_events_subject_id_users"
ADMIN_EVENTS = "'account.registration_succeeded', 'account.user_created'"
CONFIGURABLE_RECIPIENTS = "recipient_type IN ('user', 'role', 'permission')"


def upgrade() -> None:
    op.add_column("notification_events", sa.Column("subject_id", sa.UUID(), nullable=True))
    op.add_column(
        "notification_events",
        sa.Column("subject_email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("notification_events", sa.Column("source_build", sa.String(64), nullable=True))
    op.create_foreign_key(
        op.f(SUBJECT_FK),
        "notification_events",
        "users",
        ["subject_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(op.f(RECIPIENT_CHECK), "notification_routes", type_="check")
    op.create_check_constraint(
        op.f(RECIPIENT_CHECK),
        "notification_routes",
        CONFIGURABLE_RECIPIENTS
        + " OR (recipient_type = 'subject' AND recipient_value = 'subject')",
    )
    op.execute("UPDATE notification_revisions SET revision = revision + 1 WHERE id = 1")
    op.execute(
        """
        INSERT INTO notification_routes
            (id, event_type, recipient_type, recipient_value, platform_id, revision, updated_at)
        SELECT seeded.id::uuid, seeded.event_type, 'role', 'admin', 'in-app',
               revisions.revision, now()
        FROM notification_revisions AS revisions
        CROSS JOIN (VALUES
            ('00000000-0000-4000-8000-000000000102', 'account.registration_succeeded'),
            ('00000000-0000-4000-8000-000000000103', 'account.user_created')
        ) AS seeded(id, event_type)
        WHERE revisions.id = 1
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM notification_routes WHERE recipient_type = 'subject' "
        f"OR event_type IN ({ADMIN_EVENTS})"
    )
    op.execute(
        f"UPDATE notification_events SET dispatched_at = now() WHERE dispatched_at IS NULL "
        f"AND (subject_id IS NOT NULL OR source_build IS NOT NULL "
        f"OR event_type LIKE 'security.two_factor_%' OR event_type IN ({ADMIN_EVENTS}))"
    )
    op.drop_constraint(op.f(RECIPIENT_CHECK), "notification_routes", type_="check")
    op.create_check_constraint(
        op.f(RECIPIENT_CHECK), "notification_routes", CONFIGURABLE_RECIPIENTS
    )
    op.drop_constraint(op.f(SUBJECT_FK), "notification_events", type_="foreignkey")
    op.drop_column("notification_events", "subject_email_verified_at")
    op.drop_column("notification_events", "subject_id")
    op.drop_column("notification_events", "source_build")
