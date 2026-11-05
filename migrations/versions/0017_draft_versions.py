"""Article checkpoints, detached branches and expiring merge ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0017_draft_versions"
down_revision = "0016_native_shortcodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "collaboration_documents",
        sa.Column("initial_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "collaboration_documents",
        sa.Column("source_first", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "collaboration_epochs",
        sa.Column("draft_id", sa.Uuid(), sa.ForeignKey("content_drafts.id"), primary_key=True),
        sa.Column("epoch", sa.Uuid(), primary_key=True),
        sa.Column("initial_seq", sa.Integer(), nullable=False),
        sa.Column("final_seq", sa.Integer(), nullable=True),
        sa.Column("initial_state", sa.LargeBinary(), nullable=False),
    )
    op.execute(
        "INSERT INTO collaboration_epochs "
        "SELECT draft_id, epoch, 0, NULL, initial_state FROM collaboration_documents"
    )
    for column in (
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("epoch", sa.Uuid(), nullable=True),
        sa.Column("committed_seq", sa.Integer(), nullable=True),
        sa.Column("parents", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ):
        if column.name == "parents":
            from sqlalchemy.dialects.postgresql import JSONB

            column.type = JSONB()
        op.add_column("content_draft_revisions", column)
    from sqlalchemy.dialects.postgresql import JSONB

    op.create_table(
        "content_draft_branches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "draft_id", sa.Uuid(), sa.ForeignKey("content_drafts.id"), nullable=False, index=True
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("base_epoch", sa.Uuid(), nullable=True),
        sa.Column("base_seq", sa.Integer(), nullable=True),
        sa.Column("base_revision", sa.Integer(), nullable=True),
        sa.Column("base", JSONB(), nullable=False),
        sa.Column("fields", JSONB(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_revision", sa.Integer(), nullable=True),
    )
    op.create_table(
        "content_draft_merges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "draft_id", sa.Uuid(), sa.ForeignKey("content_drafts.id"), nullable=False, index=True
        ),
        sa.Column(
            "branch_id", sa.Uuid(), sa.ForeignKey("content_draft_branches.id"), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("epoch", sa.Uuid(), nullable=False),
        sa.Column("target_seq", sa.Integer(), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *[
            sa.Column(name, JSONB(), nullable=False)
            for name in ("base", "local", "remote", "result", "conflicts", "resolved")
        ],
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("content_draft_merges")
    op.drop_table("content_draft_branches")
    for column in ("deleted_at", "parents", "committed_seq", "epoch", "note"):
        op.drop_column("content_draft_revisions", column)
    op.drop_table("collaboration_epochs")
    op.drop_column("collaboration_documents", "source_first")
    op.drop_column("collaboration_documents", "initial_seq")
