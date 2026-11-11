"""Sparse custom translations layered over immutable program dictionaries."""

import sqlalchemy as sa
from alembic import op

revision = "0042_translation_overrides"
down_revision = "0041_glossary_feature_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "translation_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1 AND revision >= 1", name="singleton_revision"),
        sa.PrimaryKeyConstraint("id", name="pk_translation_revisions"),
    )
    op.execute("INSERT INTO translation_revisions (id, revision) VALUES (1, 1)")
    op.create_table(
        "translation_overrides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column("message_key", sa.String(length=512), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=4000), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=4000), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="positive_revision"),
        sa.PrimaryKeyConstraint("id", name="pk_translation_overrides"),
        sa.UniqueConstraint(
            "scope",
            "locale",
            "message_key",
            name="uq_translation_overrides_scope_locale_message_key",
        ),
    )
    op.create_index(
        "ix_translation_overrides_scope",
        "translation_overrides",
        ["scope"],
    )
    op.create_index(
        "ix_translation_overrides_locale",
        "translation_overrides",
        ["locale"],
    )


def downgrade() -> None:
    op.drop_index("ix_translation_overrides_locale", table_name="translation_overrides")
    op.drop_index("ix_translation_overrides_scope", table_name="translation_overrides")
    op.drop_table("translation_overrides")
    op.drop_table("translation_revisions")
