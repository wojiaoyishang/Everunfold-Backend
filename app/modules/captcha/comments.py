"""Submission admission: a successful CAPTCHA never grants publication approval."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import Request
from pydantic import Field
from redis.exceptions import RedisError
from sqlalchemy import DateTime, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.redis_keys import redis_key
from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.router import auth_runtime, peer
from app.modules.auth.service import AuthFailure
from app.modules.captcha.router import router
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.captcha.service import CaptchaContext, CaptchaService, context, guard
from app.modules.content.access_policy import publicly_readable
from app.modules.content.models import Comment, Content
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


class CommentReceipt(PublicModel):
    id: UUID
    status: Literal["pending"] = "pending"


class CommentSubmissionContract(PublicModel):
    request: SubmitComment
    response: PublicResponse[CommentReceipt]


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
            ex=1800,
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


@router.post("/v1/reader/comments", response_model=PublicResponse[CommentReceipt])
async def submit_comment(payload: SubmitComment, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    service = CaptchaService(runtime, peer(request), request.headers.get("Authorization"))
    policy = await service.policy("comment", payload.content_id)
    fingerprint = hashlib.sha256(
        payload.model_dump_json(exclude={"captcha", "read_receipt"}).encode()
    ).hexdigest()
    client_identity = f"{payload.client_id}:{peer(request)}"
    if policy.credential:
        client_identity += ":" + policy.credential.digest
    client_hash = hashlib.sha256(client_identity.encode()).hexdigest()
    async with runtime.database.session_factory() as session:
        existing = await session.get(CommentSubmission, payload.operation_id)
        if existing:
            if (
                not hmac.compare_digest(existing.client_hash, client_hash)
                or existing.request_hash != fingerprint
            ):
                raise AuthFailure(409, 40987, "提交标识已使用，请刷新后重试")
            await guard(session, policy)
            await admit_target(session, request, payload, policy)
            await session.commit()
            return JSONResponseSuccess(data=CommentReceipt(id=existing.id))
    try:
        if policy.credential is None:
            await service._limit("comment", 10)
    except RedisError:
        raise AuthFailure(503, 50386, "评论服务暂不可用，请重试") from None
    permit = await service.enforce("comment", payload.content_id, payload.captcha, policy=policy)
    async with runtime.database.session_factory() as session:
        await guard(session, permit)
        item = await admit_target(session, request, payload, permit)
        if not payload.author.strip() or not payload.body.strip():
            raise AuthFailure(422, 42287, "请填写称呼和评论内容")
        # Site read lock serializes with withdrawal, policy changes and publication.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:id)"), {"id": payload.operation_id.int % (2**63)}
        )
        existing = await session.get(CommentSubmission, payload.operation_id)
        if existing is None:
            session.add(
                CommentSubmission(
                    id=payload.operation_id,
                    content_id=payload.content_id,
                    client_hash=client_hash,
                    request_hash=fingerprint,
                    data={
                        "author": payload.author.strip(),
                        "body": payload.body.strip(),
                        "parent_id": str(payload.parent_id) if payload.parent_id else None,
                    },
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                Comment(
                    id=payload.operation_id,
                    content_id=item.id if item else None,
                    page_id=None if item else payload.content_id,
                    parent_id=payload.parent_id,
                    author=payload.author.strip(),
                    content=payload.body.strip(),
                    created_at=datetime.now(UTC),
                    status="pending",
                )
            )
            from app.modules.notifications.service import publish_event

            if item:
                resource_title, resource_url = item.title, item.path
            else:
                page = await session.get(ManagedPage, payload.content_id)
                assert page is not None
                published = page.published or {}
                resource_title = str(published.get("title") or "页面")
                resource_url = str(published.get("path") or "/")
            await publish_event(
                session,
                event_type="comment.pending_review",
                dedupe_key=f"comment.pending_review:{payload.operation_id}",
                resource_type="comment",
                resource_id=str(payload.operation_id),
                actor_id=None,
                data={
                    "actor": {"name": payload.author.strip()},
                    "content": {"excerpt": payload.body.strip()[:300]},
                    "resource": {
                        "title": resource_title,
                        "url": resource_url,
                        "review_url": "/admin/comments",
                    },
                },
            )
            from app.modules.auth.models import AuthAuditEvent

            session.add(AuthAuditEvent(action="comment_submit", created_at=datetime.now(UTC)))
            await session.commit()
        elif existing.request_hash != fingerprint or existing.client_hash != client_hash:
            raise AuthFailure(409, 40987, "提交标识已使用")
    return JSONResponseSuccess(data=CommentReceipt(id=payload.operation_id))
