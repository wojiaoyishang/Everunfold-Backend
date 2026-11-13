"""Keep shortcode unlock credentials and bodies outside all public projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_shortcode_regions"
down_revision = "0059_glossary_seo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shortcode_region_passwords",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
    )
    op.create_table(
        "shortcode_regions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_kind", sa.String(12), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(12), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("media_urls", postgresql.JSONB(), nullable=False),
        sa.Column(
            "credential",
            sa.Uuid(),
            sa.ForeignKey("shortcode_region_passwords.id", ondelete="RESTRICT"),
        ),
    )
    op.create_index("ix_shortcode_regions_owner_id", "shortcode_regions", ["owner_id"])


def downgrade() -> None:
    op.drop_table("shortcode_regions")
    op.drop_table("shortcode_region_passwords")
