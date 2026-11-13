"""Moderation owns one transaction, including its public release and retry receipt."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from anyio import CapacityLimiter
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.modules.auth.operations import AccountOperations
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.comments.reply_notifications import publish_reply_notice
from app.modules.comments.schemas import (
    CommentQuery,
    CommentResult,
    CommentStatus,
    EditComment,
    ModerateComments,
    ModerationCatalog,
    ModerationItem,
)
from app.modules.content.models import Comment, Content
from app.modules.content.publication import ContentPublication
from app.modules.content.schemas import ContentKind
from app.modules.media.locking import lock_references
from app.modules.pages.models import ManagedPage
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService


def target_id(comment: Comment) -> UUID:
    target = comment.content_id or comment.page_id
    if target is None:
        raise AuthFailure(409, 40989, "评论的原文不存在")
    return target


class CommentService:
    def __init__(self, session: AsyncSession, config: Settings, limiter: CapacityLimiter) -> None:
        self.session, self.config = session, config
        self.operations = AccountOperations(session, limiter)

    async def items(self, ids: list[UUID]) -> list[ModerationItem]:
        parent = aliased(Comment)
        rows = await self.session.execute(
            select(Comment, Content, ManagedPage, parent.author)
            .outerjoin(Content, Content.id == Comment.content_id)
            .outerjoin(ManagedPage, ManagedPage.id == Comment.page_id)
            .outerjoin(parent, parent.id == Comment.parent_id)
            .where(Comment.id.in_(ids))
        )
        values = {}
        for comment, content, page, reply_to in rows:
            values[comment.id] = ModerationItem(
                id=comment.id,
                revision=comment.revision,
                author=comment.author,
                body=comment.content,
                status=cast(CommentStatus, comment.status),
                created_at=comment.created_at,
                updated_at=comment.updated_at,
                parent_id=comment.parent_id,
                reply_to=reply_to,
                content_id=target_id(comment),
                content_type=cast(ContentKind, content.kind) if content else "page",
                content_title=content.title
                if content
                else str(page.data.get("title", ""))
                if page
                else "",
                content_path=content.path
                if content and content.publication_status == "published"
                else (page.published or {}).get("path")
                if page and not page.deleted_at
                else None,
            )
        return [values[identity] for identity in ids if identity in values]

    async def query(self, query: CommentQuery) -> ModerationCatalog:
        statement = (
            select(Comment)
            .outerjoin(Content, Content.id == Comment.content_id)
            .outerjoin(ManagedPage, ManagedPage.id == Comment.page_id)
        )
        if query.content_id:
            statement = statement.where(
                or_(Comment.content_id == query.content_id, Comment.page_id == query.content_id)
            )
        if query.comment_id:
            statement = statement.where(Comment.id == query.comment_id)
        if query.search.strip():
            term = (
                query.search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{term}%"
            statement = statement.where(
                or_(
                    Comment.author.ilike(pattern, escape="\\"),
                    Comment.content.ilike(pattern, escape="\\"),
                    Content.title.ilike(pattern, escape="\\"),
                    ManagedPage.data["title"].astext.ilike(pattern, escape="\\"),
                )
            )
        base = statement.subquery()
        counts = {
            str(status): int(count)
            for status, count in await self.session.execute(
                select(base.c.status, func.count()).group_by(base.c.status)
            )
        }
        if query.status:
            statement = statement.where(Comment.status == query.status)
        total = int(
            await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        ids = list(
            await self.session.scalars(
                statement.with_only_columns(Comment.id)
                .order_by(Comment.created_at.desc(), Comment.id)
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        )
        return ModerationCatalog(
            items=await self.items(ids),
            total=total,
            counts=counts,
            page=query.page,
            page_size=query.page_size,
        )

    async def mutate(
        self, actor: Actor, kind: str, payload: ModerateComments | EditComment
    ) -> CommentResult:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        remembered = await self.operations.read(
            payload.operation_id, actor.id, "comment_" + kind, payload
        )
        if remembered is not None:
            await self.session.commit()
            return CommentResult.model_validate(remembered)
        expected = (
            {item.id: item.revision for item in payload.items}
            if isinstance(payload, ModerateComments)
            else {payload.id: payload.revision}
        )
        rows = list(
            await self.session.scalars(
                select(Comment)
                .where(Comment.id.in_(expected))
                .order_by(Comment.id)
                .with_for_update()
            )
        )
        if len(rows) != len(expected):
            raise AuthFailure(404, 40489, "评论不存在")
        if any(row.revision != expected[row.id] for row in rows):
            raise AuthFailure(409, 40989, "评论已被修改，请刷新后重新选择")
        if (
            isinstance(payload, ModerateComments)
            and payload.pending_only
            and any(row.status != "pending" for row in rows)
        ):
            raise AuthFailure(409, 40989, "评论已处理，请刷新后查看")
        changed = []
        approved_replies: list[Comment] = []
        now = datetime.now(UTC)
        if kind == "reply" and isinstance(payload, EditComment):
            parent = rows[0]
            if parent.status != "approved":
                raise AuthFailure(409, 40989, "请先通过原评论，再回复")
            reply = Comment(
                id=payload.operation_id,
                content_id=parent.content_id,
                page_id=parent.page_id,
                parent_id=parent.id,
                author=actor.display_name or actor.username,
                author_user_id=actor.id,
                content=payload.body,
                created_at=now,
                updated_at=now,
                status="approved",
                revision=1,
            )
            self.session.add(reply)
            rows = [reply]
            changed.append(reply.id)
            approved_replies.append(reply)
        else:
            for row in rows:
                if isinstance(payload, ModerateComments):
                    status = {
                        "approve": "approved",
                        "pending": "pending",
                        "reject": "rejected",
                        "spam": "spam",
                        "trash": "trash",
                        "restore": row.previous_status,
                    }.get(payload.action)
                    if payload.action == "restore" and row.status != "trash":
                        raise AuthFailure(409, 40989, "只有回收站中的评论可以恢复")
                    if row.status == "trash" and payload.action not in {"trash", "restore"}:
                        raise AuthFailure(409, 40989, "请先恢复回收站中的评论")
                    if status == row.status:
                        continue
                    if payload.action == "trash":
                        row.previous_status = row.status
                    row.status = status or "pending"
                    if row.status == "approved" and row.parent_id is not None:
                        approved_replies.append(row)
                elif kind == "edit":
                    if row.status == "trash":
                        raise AuthFailure(409, 40989, "请先恢复回收站中的评论")
                    if row.content == payload.body:
                        continue
                    row.content = payload.body
                else:
                    raise ValueError("Unknown comment operation")
                row.revision += 1
                row.updated_at = now
                changed.append(row.id)
        if changed:
            await self.session.flush()
            for reply in approved_replies:
                await publish_reply_notice(self.session, reply)
            await ContentPublication(self.session, self.config).stage(
                CacheChange("comments_changed")
            )
            for row in rows:
                if row.id in changed:
                    self.session.add(
                        SettingsAudit(
                            user_id=actor.id,
                            resource="comment:" + str(row.id),
                            revision=row.revision,
                            changes={"action": kind, "status": row.status},
                        )
                    )
        result = CommentResult(items=await self.items([row.id for row in rows]))
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "comment_" + kind,
            payload,
            result.model_dump(mode="json"),
        )
        await self.session.commit()
        return result
