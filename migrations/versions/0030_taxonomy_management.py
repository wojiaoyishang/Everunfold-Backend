"""Stable term names and management revisions; existing identities and URLs remain."""

import sqlalchemy as sa
from alembic import op

revision = "0030_taxonomy"
down_revision = "0029_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "taxonomies", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "taxonomies", sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.create_table(
        "taxonomy_names",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("name_key", sa.String(200), primary_key=True),
        sa.Column("taxonomy_id", sa.Uuid(), sa.ForeignKey("taxonomies.id")),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM taxonomy_names)")):
        raise RuntimeError("Term name history exists; export and reconcile it before downgrading")
    op.drop_table("taxonomy_names")
    op.drop_column("taxonomies", "retired")
    op.drop_column("taxonomies", "revision")
