"""Explicit action registry for existing management routes; credentials never fall back silently."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime import Runtime
from app.modules.auth.key_service import identity_by_token
from app.modules.auth.models import AuthAuditEvent
from app.modules.auth.permissions import BUILTIN_APP, Actor
from app.modules.auth.resource_access import require_content_access
from app.modules.auth.router import cookie
from app.modules.auth.security import token_digest
from app.modules.auth.service import AuthFailure, AuthService

CONTENT_READ = {
    "taxonomy/query",
    "types",
    "languages",
    "editor-options",
    "actions",
    "query",
    "detail",
    "publication",
    "versions/list",
    "versions/read",
    "versions/branch/read",
    "versions/merge/read",
    "translations/query",
}
CONTENT_WRITE = {
    "batch",
    "taxonomy/preview",
    "taxonomy/save",
    "categories",
    "create",
    "save",
    "trash",
    "restore",
    "collaboration/source/acquire",
    "collaboration/source/renew",
    "collaboration/source/release",
    "collaboration/source/apply",
    "collaboration/ticket",
    "collaboration/commit",
    "versions/delete",
    "versions/branch/upload",
    "versions/branch/delete",
    "versions/merge/begin",
    "versions/merge/resolve",
    "versions/merge/renew",
    "versions/merge/cancel",
    "versions/merge/finish",
    "versions/merge/delete",
    "translations/create",
    "translations/link",
    "translations/unlink",
    "translations/review",
    "translations/trash",
    "translations/restore",
}
CONTENT_PUBLISH = {"publish", "withdraw", "sync", "schedule", "cancel-schedule"}
MEDIA_READ = {
    "/v1/admin/fonts/query",
    "/v1/admin/icons/query",
    "/v1/admin/media/query",
    "/v1/admin/media/detail",
}
MEDIA_UPLOAD = {"/v1/admin/icons/upload", "/v1/admin/media/upload"}
MEDIA_WRITE = {"/v1/admin/media/update", "/v1/admin/media/edit"}
MEDIA_DELETE = {
    "/v1/admin/media/remove-preview",
    "/v1/admin/media/remove",
    "/v1/admin/media/restore",
}


def route_permission(path: str, method: str, body: dict[str, object]) -> tuple[str, bool]:
    if path == "/v1/admin/tasks/watch":
        return ("settings:write" if body.get("resource") == "mail-test" else "settings:read"), True
    if path in {"/v1/admin/settings/mail/test", "/v1/admin/settings/mail/test/status"}:
        return "settings:write", False
    if path == "/v1/admin/settings/mail":
        return ("settings:read", True) if method == "GET" else ("settings:write", False)
    if path == "/v1/admin/runtime":
        return "settings:read", True
    if path == "/v1/admin/runtime/control":
        return "settings:write", False
    if path == "/v1/admin/workspace":
        return "workspace:read", True
    if path == "/v1/admin/notifications/inbox":
        return "workspace:read", True
    if path.startswith("/v1/admin/notifications/"):
        return ("settings:read", True) if method == "GET" else ("settings:write", False)
    if path == "/v1/admin/comments/query":
        return "comments:read", True
    if path in {
        "/v1/admin/comments/moderate",
        "/v1/admin/comments/edit",
        "/v1/admin/comments/reply",
    }:
        return "comments:moderate", False
    if path.startswith("/v1/admin/content/"):
        action = path.removeprefix("/v1/admin/content/")
        if action in {
            "components/create",
            "components/save",
            "components/publish",
            "components/schedule",
        }:
            action = action.removeprefix("components/")
        if (
            action == "collaboration/commit"
            and isinstance(body.get("action"), str)
            and body.get("action") in {"publish", "withdraw", "schedule", "cancel_schedule"}
        ):
            return "content:publish", False
        if action in CONTENT_READ:
            return "content:read", True
        if action in CONTENT_WRITE:
            return "content:write", False
        if action in CONTENT_PUBLISH:
            return "content:publish", False
    if path in MEDIA_READ:
        return "media:read", True
    if path == "/v1/admin/settings/navigation-resources":
        return "settings:read", True
    if path == "/v1/admin/extensions/usage":
        return "settings:write", True
    if path in MEDIA_UPLOAD:
        return "media:upload", False
    if path in MEDIA_WRITE:
        return "media:write", False
    if path in MEDIA_DELETE:
        return "media:delete", False
    if path == "/v1/admin/shortcodes" or path == "/v1/admin/shortcodes/preview":
        return "shortcodes:read", True
    if path == "/v1/admin/shortcodes/region-password":
        return "shortcodes:read", False
    if path in {
        "/v1/admin/shortcodes/save",
        "/v1/admin/shortcodes/apply",
        "/v1/admin/shortcodes/archive",
        "/v1/admin/shortcodes/restore",
    }:
        return "shortcodes:write", False
    if path in {
        "/v1/admin/captcha",
        "/v1/admin/settings",
        "/v1/admin/settings/translations",
        "/v1/admin/extensions",
        "/v1/admin/frontend-nodes",
        "/v1/admin/frontend-nodes/query-cache",
        "/v1/admin/frontend-nodes/{node_id}/cache-policy",
        "/v1/admin/frontend-nodes/{node_id}/cache",
        "/v1/admin/frontend-nodes/{node_id}/cache/rebuild",
        "/v1/admin/frontend-nodes/{node_id}/cache/prune",
        "/v1/admin/frontend-nodes/{node_id}/sync",
    }:
        return ("settings:read", True) if method == "GET" else ("settings:write", False)
    raise AuthFailure(403, 40364, "此接口尚未注册授权能力")


async def authorize_business(
    request: Request, session: AsyncSession, runtime: Runtime
) -> tuple[Actor, bool]:
    body: dict[str, object] = {}
    if request.method != "GET" and request.url.path != "/v1/admin/media/upload":
        try:
            value = await request.json()
            if isinstance(value, dict):
                body = value
        except ValueError:
            pass
    route = getattr(request.scope.get("route"), "path", request.url.path)
    capability, readonly = route_permission(route, request.method, body)
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthFailure(401, 40161, "API Key无效或已撤销")
        identity = await identity_by_token(session, parts[1])
        if identity is None or identity.key.audience != BUILTIN_APP:
            raise AuthFailure(401, 40161, "API Key无效或已撤销")
        actor = identity.actor()
        identity.key.last_used_at = datetime.now(UTC)
    else:
        auth = AuthService(session, runtime.settings.auth, runtime.password_limiter)
        user = await auth.require_user(
            cookie(request), request.headers.get("X-CSRF-Token"), write=request.method != "GET"
        )
        if user.must_change_password:
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        actor = Actor.browser(user, token_digest(cookie(request) or ""))
    if capability == "workspace:read":
        if actor.auth_method != "session" or not actor.permissions:
            raise AuthFailure(403, 40364, "没有执行此操作的权限")
    elif not actor.has(capability):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    if (
        route
        in {
            "/v1/admin/content/categories",
            "/v1/admin/content/publication",
            "/v1/admin/content/sync",
            "/v1/admin/content/taxonomy/query",
            "/v1/admin/content/taxonomy/preview",
            "/v1/admin/content/taxonomy/save",
        }
        and actor.permissions[capability] != "all"
    ):
        raise AuthFailure(403, 40364, "此操作需要全部内容的授权范围")
    if capability.startswith("content:") and not route.startswith("/v1/admin/content/taxonomy/"):
        ids = [body.get("id"), body.get("target_id"), request.query_params.get("id")]
        members = body.get("members")
        if isinstance(members, dict):
            ids.extend(members.keys())
        for value in ids:
            if not isinstance(value, str):
                continue
            try:
                content_id = UUID(value)
            except ValueError:
                continue
            if route.endswith("/create") and not route.endswith("/translations/create"):
                # A new identifier has no owner yet. Existing identifiers still need protection.
                from app.modules.content.draft_models import ContentDraft

                if await session.get(ContentDraft, content_id) is None:
                    continue
            await require_content_access(session, actor, capability, content_id)
        if (
            route.endswith("/collaboration/commit")
            and body.get("block_sources")
            and capability == "content:publish"
        ):
            try:
                content_id = UUID(str(body.get("id", "")))
            except ValueError as error:
                raise AuthFailure(422, 42264, "内容标识无效") from error
            await require_content_access(session, actor, "content:write", content_id)
    if actor.auth_method == "api_key" and not readonly:
        session.add(
            AuthAuditEvent(
                user_id=actor.id,
                target_user_id=actor.id,
                action="api_key_operation",
                created_at=datetime.now(UTC),
                details={"key_id": str(actor.key_id), "capability": capability, "route": route},
            )
        )
    return actor, readonly
