"""SDK page comment pagination shares the existing reader policy and unlock grant."""

from uuid import UUID

from fastapi import Request
from pydantic import Field

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.router import auth_runtime, peer
from app.modules.auth.service import AuthFailure
from app.modules.captcha.read_grants import verify_read_receipt
from app.modules.captcha.router import router
from app.modules.captcha.service import CaptchaService, guard
from app.modules.content.repository import ContentRepository
from app.modules.content.schemas import Page, PublicComment
from app.modules.content.service import PublicContentService
from app.modules.pages.models import ManagedPage


class PageCommentsRequest(PublicModel):
    id: UUID
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    read_receipt: str = Field(default="", max_length=100, repr=False)


@router.post("/v1/reader/page-comments", response_model=PublicResponse[Page[PublicComment]])
async def page_comments(payload: PageCommentsRequest, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    if payload.page_size > runtime.settings.http.max_page_size:
        raise AuthFailure(422, 42287, "每页评论数量超出限制")
    permit = await CaptchaService(
        runtime, peer(request), request.headers.get("Authorization")
    ).policy("read", payload.id)
    async with runtime.database.session_factory() as session:
        await guard(session, permit)
        if await session.get(ManagedPage, payload.id) is None:
            raise AuthFailure(404, 40486, "页面不存在")
        if permit.required:
            await verify_read_receipt(request, permit, payload.read_receipt)
        rows, parents, total = await ContentRepository(session).comments(
            payload.id, page=payload.page, page_size=payload.page_size
        )
        result = Page(
            items=PublicContentService._comment_projections(rows, parents),
            page=payload.page,
            page_size=payload.page_size,
            total=total,
        )
        await session.commit()
        return JSONResponseSuccess(data=result)
