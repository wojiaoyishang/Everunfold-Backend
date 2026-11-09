"""Non-cacheable password reads. No password or unlock grant is persisted in the browser."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicModel, PublicResponse
from app.modules.auth.repository import AuthRepository
from app.modules.auth.security import DUMMY_PASSWORD_HASH, verify_password
from app.modules.auth.service import AuthFailure
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.captcha.service import CaptchaService, guard
from app.modules.content.access_policy import password_readable
from app.modules.content.models import Content
from app.modules.content.repository import ContentRepository, presentation_conditions
from app.modules.content.router import get_session, runtime_from_request
from app.modules.content.schemas import ContentDetail
from app.modules.content.service import PublicContentService


class ContentAccessInput(PublicModel):
    id: UUID
    captcha: CaptchaAnswer | None = None
    password: str = Field(default="", max_length=200, repr=False)


class ContentAccessContract(PublicModel):
    request: ContentAccessInput
    response: PublicResponse[ContentDetail]


router = APIRouter(
    prefix="/v1/reader",
    tags=["content-access"],
    responses={
        status: {"model": ErrorResponse} for status in (403, 404, 413, 415, 422, 429, 500, 503)
    },
)


@router.post("/access", response_model=PublicResponse[ContentDetail])
async def access(
    payload: ContentAccessInput,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponseSuccess:
    runtime = runtime_from_request(request)
    ip = request.client.host if request.client else "unknown"
    permit = await CaptchaService(runtime, ip).enforce("read", payload.id, payload.captcha)
    now = datetime.now(UTC)
    ip = request.client.host if request.client else "unknown"
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
    service = PublicContentService(ContentRepository(session))
    taxonomy_map = {term.id: term for term in await service.taxonomies()}
    counts = await service.repository.comment_counts([item.id])
    result = service._detail(item, taxonomy_map, counts, authorized=True)
    from app.modules.captcha.comments import issue_read_receipt

    result.read_receipt = await issue_read_receipt(request, item.id)
    rows, parents, _ = await service.repository.comments(item.id, page=1, page_size=100)
    result.unlocked_comments = service._comment_projections(rows, parents)
    return JSONResponseSuccess(data=result)
