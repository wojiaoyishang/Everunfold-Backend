"""Retain page and template revisions and keep template drafts out of reader snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0063_page_versions"
down_revision = "0062_discussion_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("page_templates", sa.Column("draft_value", JSONB(none_as_null=True)))
    op.add_column("page_templates", sa.Column("applied_revision", sa.Integer()))
    op.execute("UPDATE page_templates SET applied_revision = revision")
    op.alter_column("page_templates", "applied_revision", nullable=False)
    for table, owner in (
        ("managed_page_revisions", "managed_pages"),
        ("page_template_revisions", "page_templates"),
    ):
        op.create_table(
            table,
            sa.Column(
                "owner_id",
                sa.Uuid(),
                sa.ForeignKey(f"{owner}.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("revision", sa.Integer(), primary_key=True),
            sa.Column("value", JSONB(), nullable=False),
            sa.Column("action", sa.String(24), nullable=False),
            sa.Column("actor", sa.String(100)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("from_revision", sa.Integer()),
        )

    # The old schema retains at most one applied page and one current draft.
    # Linked pages own settings and parameters; inherited source has its own history.
    op.execute("""
        INSERT INTO managed_page_revisions (owner_id, revision, value, action, actor, created_at)
        SELECT page.id, snapshot.revision,
               jsonb_build_object('layout', COALESCE(template.code->>'layout', 'wide'))
                   || snapshot.value,
               'baseline', audit.actor, COALESCE(audit.created_at, page.updated_at)
        FROM managed_pages AS page
        LEFT JOIN page_templates AS template ON template.id = page.template_id
        CROSS JOIN LATERAL (
            SELECT page.revision, page.data AS value
            UNION ALL
            SELECT page.applied_revision, page.applied_snapshot
            WHERE page.applied_revision <> page.revision AND page.applied_snapshot IS NOT NULL
        ) AS snapshot
        LEFT JOIN LATERAL (
            SELECT COALESCE(NULLIF(u.display_name, ''), u.username) AS actor, a.created_at
            FROM settings_audit AS a LEFT JOIN users AS u ON u.id = a.user_id
            WHERE a.resource = 'page:' || page.id::text AND a.revision = snapshot.revision
            ORDER BY a.created_at DESC LIMIT 1
        ) AS audit ON true
    """)
    op.execute("""
        INSERT INTO page_template_revisions (owner_id, revision, value, action, actor, created_at)
        SELECT template.id, template.revision,
               template.code || jsonb_build_object(
                   'title', template.title, 'description', template.description
               ),
               'baseline', audit.actor, COALESCE(audit.created_at, now())
        FROM page_templates AS template
        LEFT JOIN LATERAL (
            SELECT COALESCE(NULLIF(u.display_name, ''), u.username) AS actor, a.created_at
            FROM settings_audit AS a LEFT JOIN users AS u ON u.id = a.user_id
            WHERE a.resource = 'page-template:' || template.id::text
                AND a.revision = template.revision
            ORDER BY a.created_at DESC LIMIT 1
        ) AS audit ON true
    """)


def downgrade() -> None:
    op.drop_table("page_template_revisions")
    op.drop_table("managed_page_revisions")
    op.drop_column("page_templates", "applied_revision")
    op.drop_column("page_templates", "draft_value")
