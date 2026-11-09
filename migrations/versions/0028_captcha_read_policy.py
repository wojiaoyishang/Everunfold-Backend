"""Materialized public-read policy supports bounded, safe search and projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0028_captcha"
down_revision = "0027_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contents",
        sa.Column("captcha_read_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "reader_comment_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("content_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reader_comment_submissions_content_id", "reader_comment_submissions", ["content_id"]
    )


def downgrade() -> None:
    op.drop_table("reader_comment_submissions")
    op.drop_column("contents", "captcha_read_required")
