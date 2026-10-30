"""Public reading storage, explicit initial migration.

Revision ID: 0001_public_read
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_public_read"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author", sa.String(200), nullable=False),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("reading_minutes", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("publication_status", sa.String(16), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("public_generation", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("media_urls", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("kind IN ('article', 'page')", name="ck_contents_kind"),
        sa.CheckConstraint(
            "publication_status IN ('published', 'draft', 'withdrawn')", name="ck_contents_status"
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'unlisted', 'authenticated', 'password')",
            name="ck_contents_visibility",
        ),
        sa.CheckConstraint("public_generation >= 1", name="ck_contents_generation"),
        sa.CheckConstraint(
            "word_count >= 0 AND reading_minutes >= 1", name="ck_contents_reading_metrics"
        ),
        sa.CheckConstraint("left(path, 1) = '/'", name="ck_contents_absolute_path"),
        sa.CheckConstraint("jsonb_typeof(content) = 'array'", name="ck_contents_content_blocks"),
        sa.PrimaryKeyConstraint("id", name="pk_contents"),
        sa.UniqueConstraint("path", name="uq_contents_path"),
        sa.UniqueConstraint("source", "source_id", name="uq_contents_source"),
    )
    for column in ("kind", "published_at", "publication_status", "visibility"):
        op.create_index(f"ix_contents_{column}", "contents", [column])
    op.create_table(
        "taxonomies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('category', 'tag')", name="ck_taxonomies_kind"),
        sa.PrimaryKeyConstraint("id", name="pk_taxonomies"),
        sa.UniqueConstraint("kind", "slug", name="uq_taxonomies_kind"),
        sa.UniqueConstraint("kind", "source_id", name="uq_taxonomies_kind_source_id"),
    )
    op.create_table(
        "content_taxonomies",
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("taxonomy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["taxonomy_id"], ["taxonomies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("content_id", "taxonomy_id", name="pk_content_taxonomies"),
    )
    op.create_table(
        "comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "status IN ('approved', 'pending', 'hidden')", name="ck_comments_status"
        ),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["comments.id"]),
        sa.PrimaryKeyConstraint("id", name="pk_comments"),
        sa.UniqueConstraint("source_id", name="uq_comments_source_id"),
    )
    op.create_index("ix_comments_content_id", "comments", ["content_id"])
    op.create_index("ix_comments_created_at", "comments", ["created_at"])
    op.create_table(
        "search_documents",
        sa.Column("content_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["content_id"], ["contents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("content_id", name="pk_search_documents"),
    )
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("source_epoch", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_site_settings_singleton"),
        sa.CheckConstraint("generation >= 1", name="ck_site_settings_generation"),
        sa.PrimaryKeyConstraint("id", name="pk_site_settings"),
    )


def downgrade() -> None:
    # Only an explicit operator command can execute this destructive direction.
    for table in (
        "site_settings",
        "search_documents",
        "comments",
        "content_taxonomies",
        "taxonomies",
        "contents",
    ):
        op.drop_table(table)
