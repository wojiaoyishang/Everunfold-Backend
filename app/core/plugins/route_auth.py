"""Plugin routes use the host's current sessions, key ceilings and revocation fences."""

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plugins.routes import RoutePolicy
from app.core.runtime import Runtime
from app.modules.auth.credentials_models import ApiApplication, ApiKey
from app.modules.auth.key_service import identity_by_digest, identity_by_token
from app.modules.auth.models import AuthAuditEvent, BrowserSession, User
from app.modules.auth.permissions import BUILTIN_APP, CAPABILITIES, Actor
from app.modules.auth.router import cookie
from app.modules.auth.security import token_digest
from app.modules.auth.service import AuthFailure, AuthService


async def route_actor(
    request: Request, session: AsyncSession, runtime: Runtime, policy: RoutePolicy
) -> Actor | None:
    authorization = request.headers.get("Authorization")
    if policy.access == "public" and authorization is None:
        return None
    write = request.method not in {"GET", "HEAD", "OPTIONS"}
    if authorization is not None:
        if policy.access == "session":
            raise AuthFailure(403, 40362, "此操作需要登录会话")
        parts = authorization.split()
        identity = (
            await identity_by_token(session, parts[1])
            if len(parts) == 2 and parts[0].lower() == "bearer"
            else None
        )
        if identity is None or identity.key.audience != BUILTIN_APP:
            raise AuthFailure(401, 40161, "API Key无效或已撤销")
        # Owner -> application -> key matches account changes and CAPTCHA admission.
        await session.scalar(
            select(User).where(User.id == identity.user.id).with_for_update(read=True)
        )
        await session.scalar(
            select(ApiApplication)
            .where(ApiApplication.id == identity.app.id)
            .with_for_update(read=True)
        )
        await session.scalar(select(ApiKey).where(ApiKey.id == identity.key.id).with_for_update())
        identity = await identity_by_digest(session, identity.key.token_hash)
        if identity is None or identity.key.audience != BUILTIN_APP:
            raise AuthFailure(401, 40161, "API Key无效或已撤销")
        actor = identity.actor()
        identity.key.last_used_at = datetime.now(UTC)
        if policy.permission and not CAPABILITIES[policy.permission][1]:
            raise AuthFailure(403, 40364, "此能力不允许API Key访问")
    else:
        auth = AuthService(session, runtime.settings.auth, runtime.password_limiter)
        user = await auth.require_user(
            cookie(request), request.headers.get("X-CSRF-Token"), write=write
        )
        await session.scalar(
            select(User)
            .where(User.id == user.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        await session.scalar(
            select(BrowserSession)
            .where(BrowserSession.token_hash == token_digest(cookie(request) or ""))
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        user = await auth.require_user(
            cookie(request), request.headers.get("X-CSRF-Token"), write=write
        )
        if user.must_change_password:
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        actor = Actor.browser(user, token_digest(cookie(request) or ""))
    if policy.permission and (
        not actor.has(policy.permission)
        or (policy.scope == "all" and actor.permissions[policy.permission] != "all")
    ):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    if write:
        session.add(
            AuthAuditEvent(
                user_id=actor.id,
                target_user_id=actor.id,
                action="plugin_route",
                created_at=datetime.now(UTC),
                details={
                    "route": getattr(request.scope.get("route"), "path", request.url.path),
                    "method": request.method,
                    "auth_method": actor.auth_method,
                    "key_id": str(actor.key_id) if actor.key_id else None,
                    "capability": policy.permission,
                },
            )
        )
    return actor
