"""Pending registration and invitations with all-address uniqueness preflight."""

import sqlalchemy as sa
from alembic import op

revision = "0050_registration"
down_revision = "0049_two_factor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text("""
        SELECT lower(btrim(email)) AS email, count(*) AS users
        FROM users WHERE btrim(email) <> ''
        GROUP BY lower(btrim(email)) HAVING count(*) > 1
        ORDER BY users DESC, email LIMIT 20
    """)
    ).all()
    if duplicates:
        report = ", ".join(f"{email} ({count})" for email, count in duplicates)
        raise RuntimeError(f"Duplicate normalized account emails require review: {report}")
    op.create_index(
        "uq_users_email_normalized",
        "users",
        [sa.text("lower(btrim(email))")],
        unique=True,
        postgresql_where=sa.text("btrim(email) <> ''"),
    )
    op.create_table(
        "registration_invites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("invite_id", sa.Uuid(), sa.ForeignKey("registration_invites.id")),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("delivery_code", sa.Text(), nullable=False),
        sa.Column("mail_profile_id", sa.String(64)),
        sa.Column("mail_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    for name in ("session_hash", "username", "email", "status", "created_at", "expires_at"):
        op.create_index(f"ix_pending_registrations_{name}", "pending_registrations", [name])


def downgrade() -> None:
    op.drop_table("pending_registrations")
    op.drop_table("registration_invites")
    op.drop_index("uq_users_email_normalized", table_name="users")
