"""Durable administrator test mail, without SMTP credentials in task records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_mail_test"
down_revision = "0032_runtime_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_test_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(254), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mail_test_deliveries_user_id", "mail_test_deliveries", ["user_id"])
    op.create_index("ix_mail_test_deliveries_status", "mail_test_deliveries", ["status"])


def downgrade() -> None:
    op.drop_table("mail_test_deliveries")
