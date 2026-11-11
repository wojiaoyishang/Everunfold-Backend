"""Add the per-term image viewer preference."""

import sqlalchemy as sa
from alembic import op

revision = "0043_glossary_image_viewer"
down_revision = "0042_translation_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "glossary_terms",
        sa.Column(
            "show_image_viewer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("glossary_terms", "show_image_viewer")
