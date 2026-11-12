"""Record manual reviews and reserve verified mailbox identities."""

import sqlalchemy as sa
from alembic import op

revision = "0048_verified_email_review"
down_revision = "0047_notification_html_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    conflict = connection.execute(
        sa.text("""
        SELECT count(*) FROM (
            SELECT lower(btrim(email)) FROM users
            WHERE email_verified_at IS NOT NULL
            GROUP BY lower(btrim(email))
            HAVING count(*) > 1 OR lower(btrim(email)) = ''
        ) AS conflicts
    """)
    ).scalar_one()
    if conflict:
        raise RuntimeError(
            f"{conflict} duplicate or blank verified email groups require review before migrating"
        )
    op.add_column("users", sa.Column("email_reviewed_at", sa.DateTime(timezone=True)))
    op.create_index(
        "uq_users_verified_email",
        "users",
        [sa.text("lower(btrim(email))")],
        unique=True,
        postgresql_where=sa.text("email_verified_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_verified_email", table_name="users")
    op.drop_column("users", "email_reviewed_at")
