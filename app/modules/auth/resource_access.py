"""Owner scopes are evaluated against persisted content, including translation-group boundaries."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft


async def require_content_access(
    session: AsyncSession, actor: Actor, capability: str, content_id: UUID
) -> None:
    if not actor.has(capability):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    if actor.permissions[capability] == "all":
        return
    row = await session.get(ContentDraft, content_id)
    if row is None or row.created_by != actor.id:
        raise AuthFailure(404, 40450, "内容不存在或超出授权范围")
    foreign = await session.scalar(
        select(ContentDraft.id)
        .where(
            ContentDraft.translation_group_id == row.translation_group_id,
            ContentDraft.created_by != actor.id,
        )
        .limit(1)
    )
    if foreign is not None:
        raise AuthFailure(403, 40364, "该翻译组超出本人内容的授权范围")
