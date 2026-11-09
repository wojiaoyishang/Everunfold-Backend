"""Resolve renamed, merged and removed names without rewriting immutable history."""

import re
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.editor_schema import EditorSettings
from app.modules.content.models import Taxonomy, TaxonomyName


class TermDirectory:
    def __init__(self, terms: list[Taxonomy], aliases: list[TaxonomyName]) -> None:
        self.terms = {row.id: row for row in terms}
        self.names: dict[tuple[str, str], UUID | None] = {
            (row.kind, row.name.casefold()): row.id for row in terms if not row.retired
        }
        self.names.update({(row.kind, row.name_key): row.taxonomy_id for row in aliases})

    @classmethod
    async def load(cls, session: AsyncSession) -> "TermDirectory":
        return cls(
            list(await session.scalars(select(Taxonomy).where(Taxonomy.source_id.is_(None)))),
            list(await session.scalars(select(TaxonomyName))),
        )

    def names_for(self, kind: str, names: list[str]) -> list[str]:
        result: dict[str, str] = {}
        for name in names:
            key = (kind, name.casefold())
            if key in self.names:
                identity = self.names[key]
                row = self.terms.get(identity) if identity else None
                if not row or row.retired:
                    continue
                name = row.name
            result.setdefault(name.casefold(), name)
        return list(result.values())

    def normalize(self, settings: EditorSettings) -> EditorSettings:
        return settings.model_copy(
            update={
                "categories": self.names_for("category", settings.categories),
                "tags": self.names_for("tag", settings.tags),
            }
        )


async def ensure_term(session: AsyncSession, kind: str, name: str) -> Taxonomy | None:
    directory = await TermDirectory.load(session)
    normalized = directory.names_for(kind, [name])
    if not normalized:
        return None
    name = normalized[0]
    identity = directory.names.get((kind, name.casefold()))
    if identity:
        return directory.terms[identity]
    identity = uuid5(NAMESPACE_URL, f"everunfold:{kind}:{name.casefold()}")
    slug = re.sub(r"[^\w-]+", "-", name.casefold()).strip("-") or "term"
    if await session.scalar(
        select(Taxonomy.id).where(Taxonomy.kind == kind, Taxonomy.slug == slug)
    ):
        slug += "-" + identity.hex[:8]
    row = Taxonomy(id=identity, kind=kind, name=name, slug=slug, source_id=None)
    session.add(row)
    await session.flush()
    session.add(TaxonomyName(kind=kind, name_key=name.casefold(), taxonomy_id=row.id))
    await session.flush()
    return row


async def register_draft_terms(session: AsyncSession, settings: EditorSettings) -> None:
    """Index freely entered draft terms for administration before first publication."""
    directory = await TermDirectory.load(session)
    for kind, names in (("category", settings.categories), ("tag", settings.tags)):
        for name in names:
            if (kind, name.casefold()) not in directory.names:
                await ensure_term(session, kind, name)
