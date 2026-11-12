"""Allow single-use registration invitations without an expiration date."""

import sqlalchemy as sa
from alembic import op

revision = "0051_invitation_expiry"
down_revision = "0050_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("registration_invites", "expires_at", nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM registration_invites WHERE expires_at IS NULL LIMIT 1")
    ).scalar():
        raise RuntimeError("Remove or assign expiration dates to permanent invitations first")
    op.alter_column("registration_invites", "expires_at", nullable=False)
