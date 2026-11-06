"""Independent authored page languages and retained applied sources for directory toggles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0026_page_languages"
down_revision = "0025_scheduled_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_translation_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="positive_revision"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["managed_pages.id"],
            name="fk_page_groups_source",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    op.add_column("managed_pages", sa.Column("language", sa.String(32)))
    op.add_column("managed_pages", sa.Column("translation_group_id", sa.Uuid()))
    op.add_column("managed_pages", sa.Column("translated_from_revision", sa.Integer()))
    op.add_column("managed_pages", sa.Column("applied_snapshot", JSONB()))
    op.execute("""
        INSERT INTO page_translation_groups (id, source_id, revision)
        SELECT id, id, 1 FROM managed_pages WHERE source = 'custom'
    """)
    op.execute("""
        UPDATE managed_pages SET translation_group_id = id,
        language = COALESCE((SELECT data->'language_catalog'->>'default_code'
                             FROM site_settings WHERE id = 1), 'zh-CN')
        WHERE source = 'custom'
    """)
    # The primary row is authoritative. An old disabled page has no recoverable applied source;
    # its first enable must explicitly apply a reviewed draft rather than reconstructing a cache.
    op.execute("UPDATE managed_pages SET applied_snapshot = published")
    op.create_foreign_key(
        "fk_pages_translation_group",
        "managed_pages",
        "page_translation_groups",
        ["translation_group_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_unique_constraint(
        "uq_pages_group_language", "managed_pages", ["translation_group_id", "language"]
    )
    op.create_check_constraint(
        "translation_source",
        "managed_pages",
        "(source = 'custom' AND language IS NOT NULL AND translation_group_id IS NOT NULL) "
        "OR (source = 'registered' AND language IS NULL AND translation_group_id IS NULL)",
    )


def downgrade() -> None:
    raise RuntimeError("Review page language membership and applied sources before rollback")
