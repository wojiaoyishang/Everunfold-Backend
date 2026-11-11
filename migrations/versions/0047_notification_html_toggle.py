"""Add an explicit switch for sending HTML notification bodies."""

import sqlalchemy as sa
from alembic import op

revision = "0047_notification_html_toggle"
down_revision = "0046_multiple_mail_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_templates",
        sa.Column(
            "html_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE notification_templates
        SET html_enabled = true
        WHERE length(trim(html)) > 0
        """
    )


def downgrade() -> None:
    op.drop_column("notification_templates", "html_enabled")
