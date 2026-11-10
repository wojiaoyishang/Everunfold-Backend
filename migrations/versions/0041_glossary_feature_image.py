"""Add a validated media-library feature image to glossary terms."""

import sqlalchemy as sa
from alembic import op

revision = "0041_glossary_feature_image"
down_revision = "0040_glossary_hook_scripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "glossary_terms",
        sa.Column(
            "feature_media_id",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("glossary_terms", "feature_media_id")
