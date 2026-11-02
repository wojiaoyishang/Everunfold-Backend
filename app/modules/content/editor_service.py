"""Shared editor validation, media resolution and taxonomy publication."""

import re
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.content.editor_schema import (
    EditorCatalog,
    EditorSettings,
    EditorTerm,
    EditorTermCreate,
)
from app.modules.content.models import Content, ContentTaxonomy, Taxonomy
from app.modules.content.schemas import MediaURL
from app.modules.media.models import MediaAsset

MEDIA_REFERENCE = re.compile(r"site://media/([0-9a-fA-F-]{36})")


async def editor_media(
    session: AsyncSession,
    config: Settings | None,
    body: str,
    settings: EditorSettings,
    *,
    strict: bool = True,
) -> dict[str, MediaURL]:
    ids: set[UUID] = set()
    for value in MEDIA_REFERENCE.findall(body):
        try:
            ids.add(UUID(value))
        except ValueError as exc:
            raise AuthFailure(422, 42257, "媒体引用格式不正确") from exc
    if settings.cover_media_id:
        ids.add(settings.cover_media_id)
    if not ids or config is None:
        return {}
    rows = list(await session.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids))))
    available = {
        row.id: row
        for row in rows
        if row.status == "ready"
        and not row.deleted_at
        and not row.removed_at
        and not row.purge_pending
    }
    if strict and (
        set(available) != ids
        or (
            settings.cover_media_id
            and not available[settings.cover_media_id].mime.startswith("image/")
        )
    ):
        raise AuthFailure(409, 40953, "引用的媒体不可用，封面必须是图片")
    return {
        str(key): MediaURL(original=config.media_base_url + "/" + row.object_key)
        for key, row in available.items()
    }


async def editor_catalog(session: AsyncSession) -> EditorCatalog:
    rows = await session.scalars(
        select(Taxonomy).where(Taxonomy.source_id.is_(None)).order_by(Taxonomy.name).limit(500)
    )
    counts = dict(
        (
            await session.execute(
                select(ContentTaxonomy.taxonomy_id, func.count(ContentTaxonomy.content_id))
                .join(Content, Content.id == ContentTaxonomy.content_id)
                .where(Content.source_id.is_(None), Content.publication_status == "published")
                .group_by(ContentTaxonomy.taxonomy_id)
            )
        ).all()
    )
    return EditorCatalog(
        terms=[
            EditorTerm(
                id=row.id,
                name=row.name,
                kind=row.kind,
                parent_id=row.parent_id,
                count=counts.get(row.id, 0),
            )
            for row in rows
        ]
    )


async def create_category(
    session: AsyncSession, actor_id: UUID, payload: EditorTermCreate
) -> EditorCatalog:
    from app.modules.media.locking import lock_references
    from app.modules.settings.models import SettingsAudit

    await lock_references(session)
    name = EditorSettings(categories=[payload.name]).categories[0]
    parent = await session.get(Taxonomy, payload.parent_id) if payload.parent_id else None
    if payload.parent_id and (
        parent is None or parent.kind != "category" or parent.source_id is not None
    ):
        raise AuthFailure(422, 42260, "父分类不存在")
    identity = uuid5(NAMESPACE_URL, f"everunfold:category:{name.casefold()}")
    existing = await session.get(Taxonomy, identity)
    if existing is not None:
        if existing.parent_id != payload.parent_id:
            raise AuthFailure(409, 40955, "同名分类已存在于其他层级")
    else:
        slug = re.sub(r"[^\w-]+", "-", name.casefold()).strip("-") or "term"
        if await session.scalar(
            select(Taxonomy.id).where(Taxonomy.kind == "category", Taxonomy.slug == slug)
        ):
            slug += "-" + identity.hex[:8]
        session.add(
            Taxonomy(
                id=identity,
                kind="category",
                name=name,
                slug=slug,
                source_id=None,
                parent_id=payload.parent_id,
            )
        )
        session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="content-categories",
                revision=1,
                changes={
                    "action": "create",
                    "id": str(identity),
                    "name": name,
                    "parent_id": str(payload.parent_id) if payload.parent_id else None,
                },
            )
        )
        await session.flush()
    await session.commit()
    return await editor_catalog(session)


async def publish_taxonomies(
    session: AsyncSession, content_id: UUID, settings: EditorSettings
) -> None:
    await session.execute(delete(ContentTaxonomy).where(ContentTaxonomy.content_id == content_id))
    for kind, names in (("category", settings.categories), ("tag", settings.tags)):
        for name in names:
            identity = uuid5(NAMESPACE_URL, f"everunfold:{kind}:{name.casefold()}")
            row = await session.get(Taxonomy, identity)
            if row is None:
                slug = re.sub(r"[^\w-]+", "-", name.casefold()).strip("-") or "term"
                # Existing imported terms keep their own identity and URL.
                if await session.scalar(
                    select(Taxonomy.id).where(Taxonomy.kind == kind, Taxonomy.slug == slug)
                ):
                    slug += "-" + identity.hex[:8]
                row = Taxonomy(id=identity, kind=kind, name=name, slug=slug, source_id=None)
                session.add(row)
                await session.flush()
            session.add(ContentTaxonomy(content_id=content_id, taxonomy_id=row.id))
    await session.flush()
