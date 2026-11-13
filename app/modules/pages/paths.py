"""Page paths are shared with article publishing under the reference transaction lock."""

from uuid import UUID

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import ReservedContentPath, content_path
from app.modules.content.models import Content
from app.modules.pages.models import ManagedPage


def normalize_page_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/"):
        raise AuthFailure(422, 42205, "页面 URL 必须以 / 开头。")
    if len(value) > 500:
        raise AuthFailure(422, 42208, "页面 URL 不能超过 500 个字符。")
    try:
        return content_path(value)
    except ReservedContentPath as exc:
        raise AuthFailure(422, 42207, "这个页面 URL 由系统保留，请更换路径。") from exc
    except ValueError as exc:
        raise AuthFailure(
            422, 42206, "请输入有效的站内路径，例如 /about。不能包含空格、查询参数或锚点。"
        ) from exc


async def page_path_taken(session: AsyncSession, path: str, excluding: UUID | None = None) -> bool:
    return (
        await session.scalar(
            select(ManagedPage.id)
            .where(
                ManagedPage.deleted_at.is_(None),
                ManagedPage.id != excluding if excluding else true(),
                or_(
                    ManagedPage.data["path"].astext == path,
                    ManagedPage.published["path"].astext == path,
                ),
            )
            .limit(1)
        )
        is not None
    )


async def content_path_taken(session: AsyncSession, path: str) -> bool:
    return bool(
        await session.scalar(select(Content.id).where(Content.path == path).limit(1))
        or await session.scalar(
            select(ContentDraft.id)
            .where(
                or_(
                    ContentDraft.public_path == path,
                    ContentDraft.editor_settings["path"].astext == path,
                )
            )
            .limit(1)
        )
    )
