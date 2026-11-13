"""Deliver only replies that the account holder can reach on the public site."""

from urllib.parse import quote
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.captcha.service import current_config, required
from app.modules.content.access_policy import publicly_readable
from app.modules.content.models import Comment, Content
from app.modules.notifications.catalog import COMMENT_REPLY_PUBLISHED
from app.modules.notifications.events import publish_event
from app.modules.pages.availability import disabled_page_ids
from app.modules.pages.models import ManagedPage


async def reply_recipient(session: AsyncSession, reply: Comment) -> UUID | None:
    if reply.status != "approved" or reply.parent_id is None:
        return None
    parent = await session.get(Comment, reply.parent_id)
    if (
        parent is None
        or parent.status != "approved"
        or parent.author_user_id == reply.author_user_id
        or parent.content_id != reply.content_id
        or parent.page_id != reply.page_id
    ):
        return None
    return parent.author_user_id


async def reply_link(session: AsyncSession, reply: Comment) -> str | None:
    if reply.content_id is not None:
        item = await session.get(Content, reply.content_id)
        if item is None or item.publication_status != "published" or not publicly_readable(item):
            return None
        path = item.path
    elif reply.page_id is not None:
        page = await session.get(ManagedPage, reply.page_id)
        if (
            page is None
            or page.deleted_at is not None
            or not page.published
            or not page.published.get("enabled")
            or (
                page.implementation_id is not None
                and page.implementation_id in await disabled_page_ids(session)
            )
            or required(await current_config(session), "read", "page", page.published)
        ):
            return None
        path = page.published.get("path", "")
    else:
        return None
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    return f"{quote(path, safe='/!$&()*+,;=:@-._~')}#comment-{reply.id}"


async def publish_reply_notice(session: AsyncSession, reply: Comment) -> None:
    recipient = await reply_recipient(session, reply)
    if recipient is None or not await reply_link(session, reply):
        return
    await publish_event(
        session,
        event_type=COMMENT_REPLY_PUBLISHED,
        dedupe_key=f"{COMMENT_REPLY_PUBLISHED}:{reply.id}",
        resource_type="comment",
        resource_id=str(reply.id),
        actor_id=reply.author_user_id,
        subject_id=recipient,
        data={},
    )


async def current_reply_link(
    session: AsyncSession, resource_id: str, subject_id: UUID | None
) -> str | None:
    if subject_id is None:
        return None
    try:
        identity = UUID(resource_id)
    except ValueError:
        return None
    reply = await session.get(Comment, identity)
    if reply is None or await reply_recipient(session, reply) != subject_id:
        return None
    return await reply_link(session, reply)
