"""Optional explicit matching rules; NULL keeps existing title-and-alias semantics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0038_glossary_matching_rules"
down_revision = "0037_glossary_rich_body"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "glossary_terms",
        sa.Column("auto_match_title", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("glossary_terms", sa.Column("match_rules", JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_glossary_terms_match_rules",
        "glossary_terms",
        "match_rules IS NULL OR jsonb_typeof(match_rules) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_glossary_terms_match_rules", "glossary_terms", type_="check")
    op.drop_column("glossary_terms", "match_rules")
    op.drop_column("glossary_terms", "auto_match_title")
