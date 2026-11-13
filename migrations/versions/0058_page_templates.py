"""Store reusable source independently of the pages that use it."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_page_templates"
down_revision = "0057_page_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("code", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_page_templates_positive_revision")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_templates")),
    )
    op.add_column("managed_pages", sa.Column("template_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_managed_pages_template_id_page_templates"),
        "managed_pages",
        "page_templates",
        ["template_id"],
        ["id"],
    )
    op.create_index(op.f("ix_managed_pages_template_id"), "managed_pages", ["template_id"])
    op.create_check_constraint(
        "template_source", "managed_pages", "template_id IS NULL OR source = 'custom'"
    )


def downgrade() -> None:
    # Preserve the current source for pages that will become standalone.
    op.execute(
        """
        UPDATE managed_pages AS page
        SET data = page.data || template.code,
            applied_snapshot = page.applied_snapshot || template.code
        FROM page_templates AS template
        WHERE page.template_id = template.id
        """
    )
    op.drop_constraint(op.f("ck_managed_pages_template_source"), "managed_pages", type_="check")
    op.drop_index(op.f("ix_managed_pages_template_id"), table_name="managed_pages")
    op.drop_column("managed_pages", "template_id")
    op.drop_table("page_templates")
