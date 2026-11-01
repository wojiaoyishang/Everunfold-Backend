"""Registered content working drafts, revisions and retry receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_content_drafts"
down_revision = "0008_media_safe_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("author", sa.String(100), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("body", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision >= 1", name="revision"),
        sa.CheckConstraint("jsonb_typeof(body) = 'array'", name="body_blocks"),
    )
    op.create_index("ix_content_drafts_type_id", "content_drafts", ["type_id"])
    op.create_index("ix_content_drafts_updated_at", "content_drafts", ["updated_at"])
    op.create_table(
        "content_draft_revisions",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_drafts.id"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "content_draft_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_drafts.id"),
            nullable=False,
        ),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_content_draft_receipts_draft_id", "content_draft_receipts", ["draft_id"])


def downgrade() -> None:
    raise RuntimeError("Drafts and audit history must be preserved")
