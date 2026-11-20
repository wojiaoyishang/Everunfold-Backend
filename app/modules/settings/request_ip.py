"""Admin-owned IP logging rules and a node-bound diagnostic ticket."""

import asyncio
import hashlib
import hmac
import time

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_ip import (
    REFRESH_SECONDS,
    STORE_KEY,
    RequestIPCache,
    RequestIPDiagnostic,
    SavedRequestIPPolicy,
    SaveRequestIPPolicy,
    ip_report,
)
from app.core.responses import JSONResponseSuccess
from app.core.runtime import Runtime
from app.core.schemas import PublicResponse
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.settings.models import SettingsAudit
from app.modules.settings.node_registry import read_targets
from app.modules.settings.router import Authorized

router = APIRouter(prefix="/v1/admin/settings/real-ip", tags=["settings"])
PRIVATE = {"Cache-Control": "private, no-store"}


async def read_ip_policy(session: AsyncSession) -> SavedRequestIPPolicy:
    data = await session.scalar(select(SiteSettings.data[STORE_KEY]).where(SiteSettings.id == 1))
    return SavedRequestIPPolicy.model_validate(data or {})


async def refresh_ip_policy(runtime: Runtime, cache: RequestIPCache) -> None:
    while True:
        try:
            async with asyncio.timeout(5), runtime.database.session_factory() as session:
                started = time.monotonic()
                cache.apply(await read_ip_policy(session), started)
        except Exception:
            # The policy expires locally on each process if the database stays unavailable.
            pass
        await asyncio.sleep(REFRESH_SECONDS)


def require_admin(ctx: Authorized) -> None:
    if ctx.actor.role != "admin" or ctx.actor.auth_method != "session":
        raise AuthFailure(403, 40364, "仅限通过浏览器登录的站点管理员")


@router.get("", response_model=PublicResponse[SavedRequestIPPolicy])
async def get_policy(ctx: Authorized) -> JSONResponseSuccess:
    require_admin(ctx)
    return JSONResponseSuccess(data=await read_ip_policy(ctx.service.session), headers=PRIVATE)


@router.patch("", response_model=PublicResponse[SavedRequestIPPolicy])
async def save_policy(
    payload: SaveRequestIPPolicy, ctx: Authorized, request: Request
) -> JSONResponseSuccess:
    require_admin(ctx)
    site = await ctx.service.site(locked=True)
    prior = SavedRequestIPPolicy.model_validate(site.data.get(STORE_KEY, {}))
    if prior.revision != payload.expected_revision:
        raise AuthFailure(409, 40920, "真实 IP 来源配置已改变，请重新加载后再保存")
    policy = SavedRequestIPPolicy(
        **payload.model_dump(exclude={"expected_revision"}), revision=prior.revision + 1
    )
    site.data = {**site.data, STORE_KEY: policy.model_dump(mode="json")}
    ctx.service.session.add(
        SettingsAudit(
            user_id=ctx.actor.id,
            resource="request-ip",
            revision=policy.revision,
            changes=policy.model_dump(mode="json"),
        )
    )
    await ctx.service.session.commit()
    request.app.state.ip_policy.apply(policy)
    return JSONResponseSuccess(data=policy, headers=PRIVATE)


@router.get("/diagnostics", response_model=PublicResponse[RequestIPDiagnostic])
async def diagnostics(request: Request, ctx: Authorized, node_id: str = "") -> JSONResponseSuccess:
    require_admin(ctx)
    ticket = None
    if node_id:
        node = (await read_targets(ctx.service.session, ctx.service.config)).get(node_id)
        token = node.token(ctx.service.config, node_id, "outbound") if node and node.enabled else ""
        if len(token) >= 32:
            expires = int(time.time()) + 60
            signature = hmac.new(
                token.encode(),
                f"everunfold/ip-probe/v1:{node_id}:{expires}".encode(),
                hashlib.sha256,
            ).hexdigest()
            ticket = f"{expires}.{signature}"
    return JSONResponseSuccess(
        data={
            "fastapi": ip_report(request.scope, *request.app.state.ip_policy.current()),
            "node_ticket": ticket,
        },
        headers=PRIVATE,
    )
