"""Cross-page glossary triggers are independent from article text matching."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0039_glossary_hooks"
down_revision = "0038_glossary_matching_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "glossary_hooks",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "term_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("glossary_terms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("selector", sa.String(length=256), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("targets", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('all', 'content')", name="ck_glossary_hooks_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_glossary_hooks_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_glossary_hooks_revision"),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_glossary_hooks_priority",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(targets) = 'array'",
            name="ck_glossary_hooks_targets",
        ),
    )
    op.create_index(
        "ix_glossary_hooks_public",
        "glossary_hooks",
        ["status", "enabled", "scope"],
    )
    op.create_table(
        "glossary_hook_operations",
        sa.Column("operation_id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hook_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("glossary_hooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("glossary_hook_operations")
    op.drop_index("ix_glossary_hooks_public", table_name="glossary_hooks")
    op.drop_table("glossary_hooks")
