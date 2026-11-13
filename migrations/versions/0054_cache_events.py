"""Persist cache change events and plugin delivery leases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0054_cache_events"
down_revision = "0053_frontend_node_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cache_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("data", JSONB, nullable=False),
    )
    op.create_table(
        "cache_event_deliveries",
        sa.Column(
            "event_id", UUID(as_uuid=True), sa.ForeignKey("cache_events.id"), primary_key=True
        ),
        sa.Column("plugin_id", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(48)),
    )
    op.create_index(
        "ix_cache_event_deliveries_available_at", "cache_event_deliveries", ["available_at"]
    )


def downgrade() -> None:
    op.drop_table("cache_event_deliveries")
    op.drop_table("cache_events")
