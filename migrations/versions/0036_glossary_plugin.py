"""Independent glossary entries keep article snapshots stable."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0036_glossary_plugin"
down_revision = "0035_task_parameters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "glossary_terms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("aliases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("language", "slug", name="uq_glossary_terms_language_slug"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_glossary_terms_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_glossary_terms_revision"),
        sa.CheckConstraint(
            "jsonb_typeof(aliases) = 'array'",
            name="ck_glossary_terms_aliases",
        ),
    )
    op.create_index(
        "ix_glossary_terms_public",
        "glossary_terms",
        ["language", "status", "title"],
    )
    op.create_table(
        "glossary_operations",
        sa.Column("operation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "term_id",
            UUID(as_uuid=True),
            sa.ForeignKey("glossary_terms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("glossary_operations")
    op.drop_index("ix_glossary_terms_public", table_name="glossary_terms")
    op.drop_table("glossary_terms")
