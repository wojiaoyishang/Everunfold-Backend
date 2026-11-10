"""Bounded administrator-only JavaScript mounts for glossary hooks."""

import sqlalchemy as sa
from alembic import op

revision = "0040_glossary_hook_scripts"
down_revision = "0039_glossary_hooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "glossary_hooks",
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default="selector",
        ),
    )
    op.add_column(
        "glossary_hooks",
        sa.Column(
            "script",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.create_check_constraint(
        "ck_glossary_hooks_mode",
        "glossary_hooks",
        "mode IN ('selector', 'script')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_glossary_hooks_mode",
        "glossary_hooks",
        type_="check",
    )
    op.drop_column("glossary_hooks", "script")
    op.drop_column("glossary_hooks", "mode")
