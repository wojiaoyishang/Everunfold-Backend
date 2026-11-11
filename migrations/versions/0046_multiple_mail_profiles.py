"""Track selected mail profiles on reminders, deliveries, tests and verification."""

import sqlalchemy as sa
from alembic import op

revision = "0046_multiple_mail_profiles"
down_revision = "0045_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_routes",
        sa.Column("mail_profile_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("mail_profile_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("mail_config_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "mail_test_deliveries",
        sa.Column("profile_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "email_verifications",
        sa.Column("mail_profile_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_verifications", "mail_profile_id")
    op.drop_column("mail_test_deliveries", "profile_id")
    op.drop_column("notification_deliveries", "mail_config_revision")
    op.drop_column("notification_deliveries", "mail_profile_id")
    op.drop_column("notification_routes", "mail_profile_id")
