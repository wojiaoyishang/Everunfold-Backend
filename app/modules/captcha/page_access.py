"""Authorized page reads never return their source to a shared projection."""

from fastapi import Request
from pydantic import Field
from sqlalchemy import select

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.router import auth_runtime, peer
from app.modules.auth.service import AuthFailure
from app.modules.captcha.router import router
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.captcha.service import CaptchaService, guard
from app.modules.content.access_router import ContentAccessInput
from app.modules.content.models import Comment
from app.modules.content.schemas import PublicComment
from app.modules.content.service import PublicContentService
from app.modules.pages.schemas import PublicManagedPage
from app.modules.pages.service import public_pages


class UnlockedManagedPage(PublicManagedPage):
    unlocked_comments: list[PublicComment] = Field(default_factory=list)


class PageAccessContract(PublicModel):
    request: ContentAccessInput
    answer: CaptchaAnswer
    response: PublicResponse[UnlockedManagedPage]


@router.post("/v1/reader/access-page", response_model=PublicResponse[UnlockedManagedPage])
async def access_page(payload: ContentAccessInput, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    permit = await CaptchaService(
        runtime, peer(request), request.headers.get("Authorization")
    ).enforce("read", payload.id, payload.captcha)
    async with runtime.database.session_factory() as session:
        await guard(session, permit)
        pages = await public_pages(session, authorized=True)
        page = next((page for page in pages if page.id == payload.id), None)
        if page is None:
            raise AuthFailure(404, 40486, "内容不存在")
        from app.modules.captcha.comments import issue_read_receipt

        page = UnlockedManagedPage.model_validate(page.model_dump())
        page.read_receipt = await issue_read_receipt(request, permit)
        comments = list(
            await session.scalars(
                select(Comment)
                .where(Comment.page_id == page.id, Comment.status == "approved")
                .order_by(Comment.created_at, Comment.id)
            )
        )
        page.unlocked_comments = PublicContentService._comment_projections(comments)
        await session.commit()
        return JSONResponseSuccess(data=page)
