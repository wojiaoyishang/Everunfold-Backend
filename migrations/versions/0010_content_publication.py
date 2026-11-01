"""Content publication identity and immutable delivery snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_content_publication"
down_revision = "0009_content_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_contents_ck_contents_kind"), "contents", type_="check")
    op.create_check_constraint("kind", "contents", "kind IN ('article', 'shuoshuo', 'page')")
    op.add_column(
        "site_settings",
        sa.Column("content_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE site_settings SET content_generation = generation")
    op.add_column("content_drafts", sa.Column("published_revision", sa.Integer()))
    op.add_column("content_drafts", sa.Column("public_path", sa.Text()))
    op.add_column("content_drafts", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_table(
        "content_releases",
        sa.Column("source_epoch", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Published identities and release history must be preserved")
