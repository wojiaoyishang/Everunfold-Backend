"""Persisted whole-draft Y.Doc updates and one-use WebSocket tickets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013_collaboration"
down_revision = "0012_content_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collaboration_documents",
        sa.Column(
            "draft_id", pg.UUID(as_uuid=True), sa.ForeignKey("content_drafts.id"), primary_key=True
        ),
        sa.Column("epoch", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("committed_seq", sa.Integer(), nullable=False),
        sa.Column("initial_state", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.LargeBinary(), nullable=False),
        sa.Column("media_ids", pg.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "collaboration_updates",
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("collaboration_documents.draft_id"),
            primary_key=True,
        ),
        sa.Column("update_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("committed_seq", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", "committed_seq"),
    )
    op.create_table(
        "collaboration_tickets",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column(
            "draft_id", pg.UUID(as_uuid=True), sa.ForeignKey("content_drafts.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_collaboration_tickets_draft_id", "collaboration_tickets", ["draft_id"])
    op.create_index("ix_collaboration_tickets_expires_at", "collaboration_tickets", ["expires_at"])


def downgrade() -> None:
    raise RuntimeError("Collaborative drafts and durable receipts must be preserved")
