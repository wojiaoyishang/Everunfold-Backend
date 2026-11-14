"""Add private AI writing configuration and short-lived encrypted requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0064_ai_writing"
down_revision = "0063_page_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_writing_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("fields", JSONB(), nullable=False),
        sa.Column("key_ciphertext", sa.Text()),
        sa.CheckConstraint("id = 1 AND revision >= 0", name="singleton_revision"),
    )
    op.execute(
        "INSERT INTO ai_writing_settings (id, revision, enabled, base_url, model, fields) "
        "VALUES (1, 0, false, '', '', '{}'::jsonb)"
    )
    op.create_table(
        "ai_writing_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("scene", sa.String(32)),
        sa.Column("field", sa.String(40)),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("input_ciphertext", sa.Text()),
        sa.Column("output_ciphertext", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'unknown')", name="status"
        ),
    )
    for columns in (("status", "created_at"), ("actor_id", "created_at"), ("finished_at",)):
        op.create_index(
            "ix_ai_writing_requests_" + "_".join(columns), "ai_writing_requests", columns
        )


def downgrade() -> None:
    op.drop_table("ai_writing_requests")
    op.drop_table("ai_writing_settings")
