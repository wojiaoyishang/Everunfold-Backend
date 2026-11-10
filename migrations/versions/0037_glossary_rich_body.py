"""Preserve glossary prose as article-compatible rich content blocks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0037_glossary_rich_body"
down_revision = "0036_glossary_plugin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("glossary_terms", sa.Column("body", JSONB(), nullable=True))
    op.execute(
        """
        UPDATE glossary_terms
        SET body = jsonb_build_array(
            jsonb_build_object(
                'id', md5(id::text || chr(58) || 'body')::uuid::text,
                'type', 'markdown',
                'content', body_markdown
            )
        )
        """
    )
    op.alter_column("glossary_terms", "body", nullable=False)
    op.alter_column("glossary_terms", "body_markdown", nullable=True)


def downgrade() -> None:
    op.execute(
        """
        UPDATE glossary_terms
        SET body_markdown = COALESCE(
            (
                SELECT string_agg(value->>'content', E'\\n\\n')
                FROM jsonb_array_elements(body)
            ),
            ''
        )
        """
    )
    op.alter_column("glossary_terms", "body_markdown", nullable=False)
    op.drop_column("glossary_terms", "body")
