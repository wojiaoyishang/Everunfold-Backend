"""Session-verified replies and rate-limited password access to published fragments."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from anyio import to_thread
from fastapi import APIRouter, Request
from sqlalchemy import select

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicResponse
from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import Service, auth_runtime, cookie, peer
from app.modules.auth.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.modules.auth.service import AuthFailure
from app.modules.captcha.read_grants import read_grant_target
from app.modules.captcha.service import current_config, required
from app.modules.content.access_policy import publicly_readable
from app.modules.content.models import Comment, Content
from app.modules.content.repository import presentation_conditions
from app.modules.pages.models import ManagedPage
from app.modules.settings.router import Authorized
from app.modules.shortcodes.regions import (
    RegionPassword,
    ShortcodeRegion,
    SourceType,
    open_body,
    region_cipher,
)
from app.modules.shortcodes.schemas import (
    RegionBody,
    RegionPasswordInput,
    RegionPasswordResult,
    RegionUnlock,
)

router = APIRouter(tags=["shortcode-regions"])


@router.post(
    "/v1/admin/shortcodes/region-password", response_model=PublicResponse[RegionPasswordResult]
)
async def create_region_password(
    payload: RegionPasswordInput, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    session = ctx.service.session
    now = datetime.now(UTC)
    if not await AuthRepository(session).consume_limit(
        f"region-password:{ctx.actor.id}", 30, 300, now
    ):
        await session.commit()
        raise AuthFailure(429, 42981, "操作频繁，请稍后再试")
    identity = uuid4()
    region_cipher(runtime.settings, identity)
    encoded = await to_thread.run_sync(
        hash_password, payload.password, limiter=runtime.password_limiter
    )
    session.add(RegionPassword(id=identity, password_hash=encoded))
    await session.commit()
    return JSONResponseSuccess(data=RegionPasswordResult(credential=str(identity)))


@router.post("/v1/reader/shortcode-region", response_model=PublicResponse[RegionBody])
async def unlock_region(
    payload: RegionUnlock, request: Request, service: Service
) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    session = service.session
    identity = UUID(payload.id)
    rate = AuthRepository(session)
    now = datetime.now(UTC)
    network = peer(request)
    per_peer = await rate.consume_limit(f"region-read:{network}", 40, 300, now)
    per_region = await rate.consume_limit(f"region-read:{network}:{identity}", 10, 300, now)
    await session.commit()
    if not per_peer or not per_region:
        raise AuthFailure(429, 42982, "尝试较多，请稍后重试")
    region = await session.get(ShortcodeRegion, identity)
    if region is None:
        raise AuthFailure(404, 40481, "区域不可用")
    if region.owner_kind == "content":
        item = await session.scalar(
            select(Content).where(Content.id == region.owner_id, *presentation_conditions())
        )
        if item is None:
            raise AuthFailure(404, 40481, "区域不可用")
        requires_receipt = not publicly_readable(item)
        target = Comment.content_id
    else:
        page = await session.get(ManagedPage, region.owner_id)
        if page is None or page.deleted_at is not None or not page.published:
            raise AuthFailure(404, 40481, "区域不可用")
        requires_receipt = required(await current_config(session), "read", "page", page.published)
        target = Comment.page_id
    if requires_receipt and await read_grant_target(request, session) != region.owner_id:
        raise AuthFailure(403, 40381, "请先解锁所在页面")
    if region.kind == "reply-only":
        actor = await service.require_user(cookie(request), None, write=False)
        replied = await session.scalar(
            select(Comment.id)
            .where(
                target == region.owner_id,
                Comment.author_user_id == actor.id,
                Comment.status == "approved",
            )
            .limit(1)
        )
        if replied is None:
            raise AuthFailure(403, 40382, "请登录并提交评论，评论通过审核后即可查看")
    else:
        allowed = await rate.consume_limit(f"region-password-attempt:{identity}", 60, 300, now)
        await session.commit()
        if not allowed:
            raise AuthFailure(429, 42982, "尝试较多，请稍后重试")
        credential = (
            await session.get(RegionPassword, region.credential) if region.credential else None
        )
        valid = await to_thread.run_sync(
            verify_password,
            credential.password_hash if credential else DUMMY_PASSWORD_HASH,
            payload.password,
            limiter=runtime.password_limiter,
        )
        if not valid or credential is None:
            raise AuthFailure(403, 40383, "区域口令不正确")
    result = RegionBody(
        type=cast(SourceType, region.source_type),
        source=open_body(runtime.settings, region),
        media_urls=region.media_urls,
    )
    return JSONResponseSuccess(data=result, headers={"Cache-Control": "private, no-store"})
