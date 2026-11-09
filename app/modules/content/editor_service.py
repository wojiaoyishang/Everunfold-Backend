"""Shared editor validation, media resolution and taxonomy publication."""

import re
from uuid import UUID

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
from app.modules.media.schemas import FontInfo

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
    font_ids = {UUID(value) for value in re.findall(r"ef-font-([0-9a-fA-F-]{36})", body)}
    if strict and any(key not in available or not available[key].font_info for key in font_ids):
        raise AuthFailure(409, 40953, "引用的字体不可用，请重新选择")
    return {
        str(key): MediaURL(
            original=config.media_base_url + "/" + row.object_key,
            font=FontInfo.model_validate(row.font_info) if row.font_info else None,
        )
        for key, row in available.items()
    }


async def editor_catalog(session: AsyncSession) -> EditorCatalog:
    rows = await session.scalars(
        select(Taxonomy)
        .where(Taxonomy.source_id.is_(None), Taxonomy.retired.is_(False))
        .order_by(Taxonomy.name)
        .limit(500)
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
        parent is None
        or parent.kind != "category"
        or parent.source_id is not None
        or parent.retired
    ):
        raise AuthFailure(422, 42260, "父分类不存在")
    from app.modules.content.taxonomy_names import TermDirectory, ensure_term

    directory = await TermDirectory.load(session)
    existed = ("category", name.casefold()) in directory.names
    row = await ensure_term(session, "category", name)
    if row is None:
        raise AuthFailure(409, 40955, "该名称已移除，请选择其他名称")
    if existed and row.parent_id != payload.parent_id:
        raise AuthFailure(409, 40955, "同名分类已存在于其他层级")
    if not existed:
        row.parent_id = payload.parent_id
        session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="content-categories",
                revision=1,
                changes={
                    "action": "create",
                    "id": str(row.id),
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
    from app.modules.content.taxonomy_names import TermDirectory, ensure_term

    normalized = (await TermDirectory.load(session)).normalize(settings)
    for kind, names in (("category", normalized.categories), ("tag", normalized.tags)):
        for name in names:
            row = await ensure_term(session, kind, name)
            if row:
                session.add(ContentTaxonomy(content_id=content_id, taxonomy_id=row.id))
    await session.flush()
