"""Page membership projections never expose source text or internal metadata."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.pages.models import ManagedPage, PageTranslationGroup
from app.modules.pages.schemas import PageTranslationCatalog, PageTranslationMember, PageValue


def source_value(data: dict[str, Any]) -> PageValue:
    return PageValue.model_validate(
        {key: value for key, value in data.items() if key in PageValue.model_fields}
    )


def unapplied(row: ManagedPage) -> bool:
    return row.applied_snapshot is None or source_value(row.data).model_dump(
        exclude={"enabled"}
    ) != source_value(row.applied_snapshot).model_dump(exclude={"enabled"})


async def members(session: AsyncSession, group: UUID) -> list[ManagedPage]:
    return list(
        await session.scalars(
            select(ManagedPage)
            .where(ManagedPage.translation_group_id == group)
            .order_by(ManagedPage.id)
        )
    )


async def translations(session: AsyncSession, row: ManagedPage) -> PageTranslationCatalog:
    if row.source != "custom" or row.translation_group_id is None:
        raise AuthFailure(422, 42297, "前端注册页面的多语言由代码管理")
    group = await session.get(
        PageTranslationGroup, row.translation_group_id, populate_existing=True
    )
    if group is None:
        raise AuthFailure(404, 40496, "页面语言组不存在")
    rows = await members(session, group.id)
    original = next(item for item in rows if item.id == group.source_id)
    return PageTranslationCatalog(
        id=group.id,
        source_id=group.source_id,
        revision=group.revision,
        members=[
            PageTranslationMember(
                id=item.id,
                language=item.language or "",
                title=item.data["title"],
                path=(item.published or item.data)["path"],
                revision=item.revision,
                applied_revision=item.applied_revision,
                enabled=item.published is not None,
                archived=item.deleted_at is not None,
                is_original=item.id == group.source_id,
                needs_review=item.translated_from_revision is not None
                and item.translated_from_revision
                < (original.applied_revision or original.revision),
            )
            for item in rows
        ],
    )


async def promote_original(session: AsyncSession, row: ManagedPage) -> None:
    if row.translation_group_id is None:
        return
    group = await session.get(PageTranslationGroup, row.translation_group_id)
    if group and group.source_id == row.id and row.deleted_at is not None:
        replacement = next(
            (
                item
                for item in await members(session, group.id)
                if item.id != row.id and item.deleted_at is None
            ),
            None,
        )
        if replacement:
            group.source_id = replacement.id
            replacement.translated_from_revision = None
    if group:
        group.revision += 1
