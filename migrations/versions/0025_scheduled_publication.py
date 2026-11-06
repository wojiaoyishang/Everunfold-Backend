"""Durable one-shot publication plans, frozen independently of the working document."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025_scheduled_publication"
down_revision = "0024_page_internal_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_drafts", sa.Column("scheduled_for", sa.DateTime(timezone=True)))
    op.add_column("content_drafts", sa.Column("scheduled_revision", sa.Integer()))
    op.add_column("content_drafts", sa.Column("scheduled_payload", JSONB(none_as_null=True)))
    op.add_column("content_drafts", sa.Column("schedule_error", sa.String(40)))
    op.create_index("ix_content_drafts_scheduled_for", "content_drafts", ["scheduled_for"])
    op.create_check_constraint(
        "publication_plan",
        "content_drafts",
        "(scheduled_for IS NULL AND scheduled_revision IS NULL AND scheduled_payload IS NULL) OR "
        "(scheduled_for IS NOT NULL AND scheduled_revision IS NOT NULL AND "
        "scheduled_revision >= 1 AND scheduled_payload IS NOT NULL AND "
        "jsonb_typeof(scheduled_payload) = 'object')",
    )


def downgrade() -> None:
    raise RuntimeError("Cancel and review pending publication plans before destructive rollback")
