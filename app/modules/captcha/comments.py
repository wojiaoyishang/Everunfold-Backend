"""Admit reader comments and apply the site discussion policy."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Request
from pydantic import Field
from redis.exceptions import RedisError
from sqlalchemy import DateTime, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.plugins.business_events import BusinessEvent
from app.core.plugins.business_http import mark_committed
from app.core.redis_keys import redis_key
from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.router import auth_runtime, cookie, peer
from app.modules.auth.service import AuthFailure, AuthService
from app.modules.captcha.router import router
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.captcha.service import CaptchaContext, CaptchaService, context, guard
from app.modules.comments.discussion import (
    SubmissionStatus,
    classify_comment,
    comment_website,
    require_comment_account,
    settings_from_data,
)
from app.modules.comments.discussion_schema import DiscussionPolicy, DiscussionSettings
from app.modules.content.access_policy import publicly_readable
from app.modules.content.models import Comment, Content, SiteSettings
from app.modules.content.schemas import CommentFormat, PublicComment
from app.modules.media.locking import lock_references
from app.modules.pages.models import ManagedPage


class CommentSubmission(Base):
    __tablename__ = "reader_comment_submissions"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    content_id: Mapped[UUID] = mapped_column(index=True)
    client_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubmitComment(PublicModel):
    operation_id: UUID
    content_id: UUID
    client_id: UUID
    parent_id: UUID | None = None
    author: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=5000)
    captcha: CaptchaAnswer | None = None
    read_receipt: str = Field(default="", max_length=100, repr=False)
    # Omit defaults from the request hash to preserve existing submission receipts.
    format: CommentFormat = Field(default="plain", exclude_if=lambda value: value == "plain")
    website: str | None = Field(
        default=None, max_length=300, exclude_if=lambda value: value is None
    )


class CommentReceipt(PublicModel):
    id: UUID
    status: SubmissionStatus = "pending"
    comment: PublicComment | None = Field(default=None, exclude_if=lambda value: value is None)


class CommentSubmissionContract(PublicModel):
    request: SubmitComment
    response: PublicResponse[CommentReceipt]


READ_RECEIPT_TTL = 1800


def receipt_key(request: Request, value: str) -> str:
    return redis_key(
        auth_runtime(request).settings,
        "captcha",
        "read",
        hashlib.sha256(value.encode()).hexdigest(),
    )


def receipt_data(request: Request, policy: CaptchaContext) -> dict[str, str | None]:
    return {
        "stamp": policy.stamp,
        "peer": peer(request),
        "credential": policy.credential.digest if policy.credential else None,
    }


async def issue_read_receipt(request: Request, policy: CaptchaContext) -> str:
    import secrets

    runtime = auth_runtime(request)
    value = secrets.token_urlsafe(32)
    try:
        await runtime.redis.set(
            receipt_key(request, value),
            json.dumps(receipt_data(request, policy)),
            ex=READ_RECEIPT_TTL,
        )
    except RedisError:
        raise AuthFailure(503, 50386, "验证码服务暂不可用，请重试") from None
    return value


async def admit_target(
    session: AsyncSession, request: Request, payload: SubmitComment, permit: CaptchaContext
) -> Content | None:
    """Recheck admission even when an operation already has an idempotency receipt."""
    item = await session.get(Content, payload.content_id)
    if item:
        policy = item.editor_settings or {}
        protected = not publicly_readable(item)
        target_message = "请先解锁正文再发表评论"
    else:
        page = await session.get(ManagedPage, payload.content_id)
        if not page or page.deleted_at or not page.published:
            raise AuthFailure(404, 40486, "内容不存在")
        policy = page.published
        protected = False
        target_message = "请先解锁页面再发表评论"
    if not policy.get("comments_open", True):
        raise AuthFailure(403, 40388, "此内容已关闭评论")
    read_policy = replace(
        await context(session, "read", payload.content_id), credential=permit.credential
    )
    if protected or read_policy.required:
        runtime = auth_runtime(request)
        try:
            raw = (
                await runtime.redis.get(receipt_key(request, payload.read_receipt))
                if payload.read_receipt
                else None
            )
        except RedisError:
            raise AuthFailure(503, 50386, "评论服务暂不可用") from None
        if not raw or json.loads(raw) != receipt_data(request, read_policy):
            raise AuthFailure(403, 40388, target_message)
    if payload.parent_id:
        parent = await session.get(Comment, payload.parent_id)
        if (
            not parent
            or parent.status != "approved"
            or (parent.content_id if item else parent.page_id) != payload.content_id
        ):
            raise AuthFailure(422, 42287, "回复目标不可用")
    return item


async def comment_account(session: AsyncSession, request: Request) -> User | None:
    csrf = request.headers.get("X-CSRF-Token")
    if csrf is None:
        return None
    if request.headers.get("Authorization") is not None:
        raise AuthFailure(403, 40362, "此操作需要登录会话")
    runtime = auth_runtime(request)
    return await AuthService(session, runtime.settings.auth, runtime.password_limiter).require_user(
        cookie(request), csrf, write=True
    )


async def discussion_settings(session: AsyncSession, *, locked: bool = False) -> DiscussionSettings:
    site = await session.get(
        SiteSettings,
        1,
        with_for_update={"read": True} if locked else None,
        populate_existing=locked,
    )
    if site is None:
        raise AuthFailure(503, 50320, "站点设置尚未初始化，请先执行数据库初始化")
    return settings_from_data(site.data)


@router.get("/v1/reader/discussion", response_model=PublicResponse[DiscussionPolicy])
async def read_discussion(request: Request) -> JSONResponseSuccess:
    async with auth_runtime(request).database.session_factory() as session:
        policy = await discussion_settings(session)
    return JSONResponseSuccess(
        data=DiscussionPolicy(
            allow_guest_comments=policy.allow_guest_comments,
            comments_disabled=policy.comments_disabled,
        )
    )


async def approved_receipt(session: AsyncSession, comment: Comment) -> CommentReceipt:
    from app.modules.content.service import PublicContentService

    parent = await session.get(Comment, comment.parent_id) if comment.parent_id else None
    parents = {parent.id: parent} if parent and parent.status == "approved" else {}
    public = PublicContentService._comment_projections([comment], parents)[0]
    return CommentReceipt(id=comment.id, status="approved", comment=public)


async def replay_receipt(
    session: AsyncSession, existing: CommentSubmission, client_hash: str, fingerprint: str
) -> CommentReceipt:
    if (
        not hmac.compare_digest(existing.client_hash, client_hash)
        or existing.request_hash != fingerprint
    ):
        raise AuthFailure(409, 40987, "提交标识已使用，请刷新后重试")
    if existing.status == "approved":
        row = await session.get(Comment, existing.id)
        if row and row.status == "approved":
            return await approved_receipt(session, row)
    return CommentReceipt(id=existing.id)


async def publish_pending_notice(
    session: AsyncSession, comment: Comment, item: Content | None
) -> None:
    from app.modules.notifications.events import publish_event

    if item:
        resource_title, resource_url = item.title, item.path
    else:
        page = await session.get(ManagedPage, comment.page_id)
        assert page is not None
        published = page.published or {}
        resource_title = str(published.get("title") or "页面")
        resource_url = str(published.get("path") or "/")
    await publish_event(
        session,
        event_type="comment.pending_review",
        dedupe_key=f"comment.pending_review:{comment.id}",
        resource_type="comment",
        resource_id=str(comment.id),
        actor_id=comment.author_user_id,
        data={
            "actor": {"name": comment.author},
            "content": {"excerpt": comment.content[:300]},
            "resource": {
                "title": resource_title,
                "url": resource_url,
                "review_url": "/admin/comments",
            },
        },
    )


@router.post("/v1/reader/comments", response_model=PublicResponse[CommentReceipt])
async def submit_comment(payload: SubmitComment, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    service = CaptchaService(runtime, peer(request), request.headers.get("Authorization"))
    policy = await service.policy("comment", payload.content_id)
    payload.website = comment_website(payload.website)
    fingerprint = hashlib.sha256(
        payload.model_dump_json(exclude={"captcha", "read_receipt"}).encode()
    ).hexdigest()
    client_identity = f"{payload.client_id}:{peer(request)}"
    if policy.credential:
        client_identity += ":" + policy.credential.digest
    async with runtime.database.session_factory() as session:
        account = await comment_account(session, request)
        require_comment_account(await discussion_settings(session), account)
        account_id = account.id if account else None
        if account_id is not None:
            client_identity += f":account:{account_id}"
        client_hash = hashlib.sha256(client_identity.encode()).hexdigest()
        existing = await session.get(CommentSubmission, payload.operation_id)
        if existing:
            await guard(session, policy)
            require_comment_account(await discussion_settings(session), account)
            await admit_target(session, request, payload, policy)
            replayed = await replay_receipt(session, existing, client_hash, fingerprint)
            await session.commit()
            return JSONResponseSuccess(data=replayed)
    try:
        if policy.credential is None:
            await service._limit("comment", 10)
    except RedisError:
        raise AuthFailure(503, 50386, "评论服务暂不可用，请重试") from None
    permit = await service.enforce("comment", payload.content_id, payload.captcha, policy=policy)
    async with runtime.database.session_factory() as session:
        account = await comment_account(session, request)
        if account is not None:
            # Account profile updates acquire the user lock before the media lock.
            # Refresh mailbox proof under that lock before testing automatic approval.
            account = await session.scalar(
                select(User)
                .where(User.id == account.id, User.active.is_(True))
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
            await lock_references(session)
        if (account.id if account else None) != account_id:
            raise AuthFailure(409, 40987, "登录状态已更新，请重新检查后重试")
        await guard(session, permit, publishing=account is not None)
        discussion = await discussion_settings(session, locked=True)
        require_comment_account(discussion, account)
        item = await admit_target(session, request, payload, permit)
        author = (account.display_name or account.username) if account else payload.author.strip()
        body = payload.body.strip()
        if not author or not body:
            raise AuthFailure(422, 42287, "请填写称呼和评论内容")
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:id)"), {"id": payload.operation_id.int % (2**63)}
        )
        existing = await session.get(CommentSubmission, payload.operation_id)
        if existing is not None:
            result = await replay_receipt(session, existing, client_hash, fingerprint)
            await session.commit()
            return JSONResponseSuccess(data=result)
        status = await classify_comment(
            session, discussion, account, author=author, body=body, website=payload.website
        )
        now = datetime.now(UTC)
        session.add(
            CommentSubmission(
                id=payload.operation_id,
                content_id=payload.content_id,
                client_hash=client_hash,
                request_hash=fingerprint,
                data={
                    "author": author,
                    "body": body,
                    "parent_id": str(payload.parent_id) if payload.parent_id else None,
                    "format": payload.format,
                    "website": payload.website,
                },
                created_at=now,
                status=status,
            )
        )
        comment = Comment(
            id=payload.operation_id,
            content_id=item.id if item else None,
            page_id=None if item else payload.content_id,
            parent_id=payload.parent_id,
            author=author,
            author_user_id=account_id,
            content=body,
            body_format=payload.format,
            website=payload.website,
            created_at=now,
            status=status,
        )
        session.add(comment)
        if status == "approved":
            from app.modules.comments.reply_notifications import publish_reply_notice
            from app.modules.content.publication import ContentPublication
            from app.modules.settings.cache_event_schema import CacheChange

            await ContentPublication(session, runtime.settings).stage(
                CacheChange("comments_changed")
            )
            await publish_reply_notice(session, comment)
            result = await approved_receipt(session, comment)
        else:
            await publish_pending_notice(session, comment, item)
            result = CommentReceipt(id=comment.id)

        session.add(
            AuthAuditEvent(
                user_id=account_id,
                action="comment_submit",
                created_at=now,
                details={"status": status},
            )
        )
        await session.commit()
    mark_committed(
        BusinessEvent(
            "comment_submit",
            "success",
            state=status,
            resource_id=comment.id,
            resource_kind=item.kind if item else "page",
        )
    )
    return JSONResponseSuccess(data=result)
