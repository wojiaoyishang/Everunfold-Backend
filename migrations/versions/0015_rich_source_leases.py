"""Durable source edit ownership and idempotent source applications."""

import sqlalchemy as sa
from alembic import op

revision = "0015_rich_source_leases"
down_revision = "0014_fonts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collaboration_updates", sa.Column("source_signature", sa.String(64), nullable=True)
    )
    op.create_table(
        "collaboration_source_leases",
        sa.Column(
            "draft_id",
            sa.Uuid(),
            sa.ForeignKey("collaboration_documents.draft_id"),
            primary_key=True,
        ),
        sa.Column("block_id", sa.Uuid(), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("session_hash", sa.String(64), nullable=True),
        sa.Column("client_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("collaboration_source_leases")
    op.drop_column("collaboration_updates", "source_signature")
