"""Independent language versions, one constrained group and one list anchor."""

import sqlalchemy as sa
from alembic import op

revision = "0018_content_languages"
down_revision = "0017_draft_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_translation_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("type_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["content_drafts.id"],
            name="fk_translation_groups_source",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("revision >= 1", name="revision"),
    )
    op.add_column(
        "content_drafts",
        sa.Column("language", sa.String(32), nullable=False, server_default="zh-CN"),
    )
    op.add_column("content_drafts", sa.Column("translation_group_id", sa.Uuid(), nullable=True))
    op.add_column(
        "content_drafts", sa.Column("translated_from_revision", sa.Integer(), nullable=True)
    )
    # Existing public language is authoritative; unpublished drafts use the site default.
    op.execute(
        "UPDATE content_drafts d SET language = c.language FROM contents c WHERE c.id = d.id"
    )
    op.execute(
        "INSERT INTO content_translation_groups (id, source_id, type_id, revision) "
        "SELECT id, id, type_id, 1 FROM content_drafts"
    )
    op.execute("UPDATE content_drafts SET translation_group_id = id")
    op.alter_column("content_drafts", "translation_group_id", nullable=False)
    op.create_foreign_key(
        "fk_content_drafts_translation_group",
        "content_drafts",
        "content_translation_groups",
        ["translation_group_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_unique_constraint(
        "uq_content_drafts_group_language", "content_drafts", ["translation_group_id", "language"]
    )
    op.add_column(
        "contents", sa.Column("translation_group_id", sa.Uuid(), nullable=True, index=True)
    )
    op.execute(
        "UPDATE contents c SET translation_group_id = d.translation_group_id "
        "FROM content_drafts d WHERE d.id = c.id"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Language identity is durable; restore a reviewed backup instead of dropping it"
    )
