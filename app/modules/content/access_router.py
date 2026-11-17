"""Private article reads using a password or an unexpired reading receipt."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicModel, PublicResponse
from app.modules.auth.repository import AuthRepository
from app.modules.auth.security import DUMMY_PASSWORD_HASH, verify_password
from app.modules.auth.service import AuthFailure
from app.modules.captcha.read_grants import READ_RECEIPT_HEADER, verify_read_receipt
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.captcha.service import CaptchaContext, CaptchaService, guard
from app.modules.content.access_policy import password_readable
from app.modules.content.models import Content
from app.modules.content.repository import ContentRepository, presentation_conditions
from app.modules.content.router import get_session, runtime_from_request
from app.modules.content.schemas import ContentDetail
from app.modules.content.service import PublicContentService


class ContentReadTarget(PublicModel):
    id: UUID


class ContentAccessInput(ContentReadTarget):
    captcha: CaptchaAnswer | None = None
    password: str = Field(default="", max_length=200, repr=False)


class UnlockedArticleExcerpt(PublicModel):
    id: UUID
    excerpt: str


class ContentAccessContract(PublicModel):
    request: ContentAccessInput
    response: PublicResponse[ContentDetail]
    read_target: ContentReadTarget
    excerpt_response: PublicResponse[UnlockedArticleExcerpt]


router = APIRouter(
    prefix="/v1/reader",
    tags=["content-access"],
    responses={
        status: {"model": ErrorResponse}
        for status in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
    },
)
Session = Annotated[AsyncSession, Depends(get_session)]
ReadReceipt = Annotated[str, Header(alias=READ_RECEIPT_HEADER, max_length=100)]


async def unlocked_detail(session: AsyncSession, item: Content, receipt: str) -> ContentDetail:
    service = PublicContentService(ContentRepository(session))
    taxonomy_map = {term.id: term for term in await service.taxonomies()}
    counts = await service.repository.comment_counts([item.id])
    result = service._detail(item, taxonomy_map, counts, authorized=True)
    result.read_receipt = receipt
    rows, parents, _ = await service.repository.comments(item.id, page=1, page_size=100)
    result.unlocked_comments = service._comment_projections(rows, parents)
    return result


@router.post("/access", response_model=PublicResponse[ContentDetail])
async def access(
    payload: ContentAccessInput,
    request: Request,
    session: Session,
) -> JSONResponseSuccess:
    runtime = runtime_from_request(request)
    ip = request.client.host if request.client else "unknown"
    permit = await CaptchaService(runtime, ip, request.headers.get("Authorization")).enforce(
        "read", payload.id, payload.captcha
    )
    if permit.credential is None:
        now = datetime.now(UTC)
        allowed = await AuthRepository(session).consume_limit(f"content-access:{ip}", 30, 300, now)
        per_content = await AuthRepository(session).consume_limit(
            f"content-access:{ip}:{payload.id}", 10, 300, now
        )
        await session.commit()
        if not allowed or not per_content:
            raise AuthFailure(429, 42950, "尝试较多，请稍后重试")
    await guard(session, permit)
    item = await session.scalar(
        select(Content)
        .where(Content.id == payload.id, *presentation_conditions())
        .options(selectinload(Content.taxonomies))
        .with_for_update()
    )
    if item is None:
        raise AuthFailure(404, 40450, "内容不存在")
    if not password_readable(item):
        valid = await to_thread.run_sync(
            verify_password,
            item.access_password_hash or DUMMY_PASSWORD_HASH,
            payload.password,
            limiter=runtime.password_limiter,
        )
        if not valid:
            raise AuthFailure(403, 40350, "密码不正确，或内容尚未开放")
    from app.modules.captcha.comments import issue_read_receipt

    result = await unlocked_detail(session, item, await issue_read_receipt(request, permit))
    await session.commit()
    return JSONResponseSuccess(data=result)


async def article_read_permit(request: Request, id_: UUID, receipt: str) -> CaptchaContext:
    if not receipt:
        raise AuthFailure(403, 40388, "阅读验证已过期，请重新解锁正文")
    permit = await CaptchaService(
        runtime_from_request(request),
        request.client.host if request.client else "unknown",
        request.headers.get("Authorization"),
    ).policy("read", id_)
    await verify_read_receipt(request, permit, receipt)
    return permit


def remembered_article(id_: UUID) -> tuple[ColumnElement[bool], ...]:
    return (
        Content.id == id_,
        Content.kind == "article",
        Content.visibility == "password",
        *presentation_conditions(),
    )


@router.post("/access/resume", response_model=PublicResponse[ContentDetail])
async def resume_article(
    payload: ContentReadTarget, request: Request, session: Session, receipt: ReadReceipt = ""
) -> JSONResponseSuccess:
    permit = await article_read_permit(request, payload.id, receipt)
    item = await session.scalar(
        select(Content)
        .where(*remembered_article(payload.id))
        .options(selectinload(Content.taxonomies))
    )
    if item is None:
        raise AuthFailure(404, 40450, "内容不存在")
    # Recheck after loading the row so a concurrent edit cannot expose a newer revision.
    await guard(session, permit)
    return JSONResponseSuccess(data=await unlocked_detail(session, item, receipt))


@router.post("/access/excerpt", response_model=PublicResponse[UnlockedArticleExcerpt])
async def article_excerpt(
    payload: ContentReadTarget, request: Request, session: Session, receipt: ReadReceipt = ""
) -> JSONResponseSuccess:
    permit = await article_read_permit(request, payload.id, receipt)
    row = (
        await session.execute(
            select(Content.excerpt, Content.editor_settings).where(*remembered_article(payload.id))
        )
    ).one_or_none()
    if row is None:
        raise AuthFailure(404, 40450, "内容不存在")
    await guard(session, permit)
    excerpt = "" if (row.editor_settings or {}).get("hide_card_excerpt") is True else row.excerpt
    return JSONResponseSuccess(data=UnlockedArticleExcerpt(id=payload.id, excerpt=excerpt))
