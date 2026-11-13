"""Add the opt-in, backend-owned frontend node registry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0053_frontend_node_registry"
down_revision = "0052_password_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frontend_node_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("nodes", JSONB, nullable=False),
        sa.Column("bindings", JSONB, nullable=False),
        sa.CheckConstraint(
            "id = 1 AND revision >= 1", name=op.f("ck_frontend_node_registry_revision")
        ),
    )


def downgrade() -> None:
    op.drop_table("frontend_node_registry")
