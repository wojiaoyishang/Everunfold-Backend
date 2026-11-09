"""Index existing draft-only terms without changing content or historic snapshots."""

import re
from uuid import NAMESPACE_URL, uuid5

from alembic import op
from sqlalchemy import text

revision = "0031_draft_terms"
down_revision = "0030_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    known = set(connection.execute(text("SELECT kind, name_key FROM taxonomy_names")).all())
    terms = connection.execute(
        text("SELECT id, kind, name, slug FROM taxonomies WHERE source_id IS NULL AND NOT retired")
    ).all()
    known.update((row.kind, row.name.casefold()) for row in terms)
    slugs = {
        (row.kind, row.slug)
        for row in connection.execute(text("SELECT kind, slug FROM taxonomies"))
    }
    for (settings,) in connection.execute(text("SELECT editor_settings FROM content_drafts")):
        for kind, key in (("category", "categories"), ("tag", "tags")):
            for name in (settings or {}).get(key, []):
                if not name or (kind, name.casefold()) in known:
                    continue
                identity = uuid5(NAMESPACE_URL, f"everunfold:{kind}:{name.casefold()}")
                slug = re.sub(r"[^\w-]+", "-", name.casefold()).strip("-") or "term"
                if (kind, slug) in slugs:
                    slug += "-" + identity.hex[:8]
                connection.execute(
                    text(
                        "INSERT INTO taxonomies (id, kind, name, slug, revision, retired) "
                        "VALUES (:id,:kind,:name,:slug,1,false)"
                    ),
                    dict(id=identity, kind=kind, name=name, slug=slug),
                )
                connection.execute(
                    text(
                        "INSERT INTO taxonomy_names (kind,name_key,taxonomy_id) "
                        "VALUES (:kind,:name,:id)"
                    ),
                    dict(kind=kind, name=name.casefold(), id=identity),
                )
                known.add((kind, name.casefold()))
                slugs.add((kind, slug))


def downgrade() -> None:
    # Term identities may already be in use; keep the harmless index rows.
    pass
