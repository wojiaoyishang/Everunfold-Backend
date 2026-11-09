"""Private invocation parameters remain outside broker payloads and runtime projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0035_task_parameters"
down_revision = "0034_task_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_task_runs",
        sa.Column("parameters", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("runtime_task_runs", "parameters")
