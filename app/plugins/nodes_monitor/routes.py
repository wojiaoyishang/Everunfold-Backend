"""A single private HTML page with an independent session."""

import asyncio
import hashlib
from base64 import b64encode
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import ClientDisconnect
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.query_cache_schemas import QueryCacheReportRequest
from app.core.runtime import Runtime
from app.modules.auth.service import AuthFailure
from app.modules.settings.nodes_schemas import MonitorStatus, NodeChange

from .management import manage_node
from .security import COOKIE_NAME, LOGIN_WINDOW, MonitorAccess
from .status import read_status

PREFIX = "/_nodes"


def monitor_router(access: MonitorAccess | None) -> APIRouter:
    router = APIRouter(prefix=PREFIX, include_in_schema=False)
    template = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    script_hash = _inline_hash(template, "script")
    style_hash = _inline_hash(template, "style")
    headers = {
        "Cache-Control": "private, no-store",
        "X-Robots-Tag": "noindex, nofollow",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Content-Security-Policy": (
            f"default-src 'none'; style-src 'sha256-{style_hash}'; "
            f"script-src 'sha256-{script_hash}'; connect-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        ),
    }
    refresh_lock = asyncio.Lock()
    cached: tuple[float, MonitorStatus] | None = None

    def unavailable(request: Request) -> Response | None:
        if access is None:
            return HTMLResponse("节点监控尚未配置会话密钥。", 503, headers)
        if not access.accepts_host(request):
            if (
                request.method == "GET"
                and request.url.path in {PREFIX, PREFIX + "/"}
                and request.headers.get("host", "").lower() != access.host
            ):
                return RedirectResponse(access.origin + PREFIX, 307, headers=headers)
            return Response(status_code=404, headers=headers)
        return None

    def page(
        mode: Literal["login", "dashboard"],
        message: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        return HTMLResponse(
            template.replace("{{mode}}", mode).replace("{{message}}", message),
            status,
            headers,
        )

    @router.get("")
    async def index(request: Request) -> Response:
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        return page("dashboard" if access.authenticated(request) else "login")

    @router.get("/")
    async def canonical(request: Request) -> Response:
        blocked = unavailable(request)
        return blocked if blocked is not None else RedirectResponse(PREFIX, 307, headers=headers)

    @router.post("/login")
    async def login(request: Request) -> Response:
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.accepts_post(request):
            return page("login", "请求来源无效。", 403)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return page("login", "请使用页面上的密码表单。", 415)
        peer = request.client.host if request.client else ""
        if not access.allow_attempt(peer):
            throttled = page("login", "尝试次数过多，请稍后再试。", 429)
            throttled.headers["Retry-After"] = str(LOGIN_WINDOW)
            return throttled
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return page("login", "请求过大。", 413)
                    body.extend(chunk)
            values = parse_qs(
                body.decode("ascii"),
                max_num_fields=1,
                keep_blank_values=True,
                errors="strict",
            ).get("password", [])
        except (UnicodeError, ValueError, TimeoutError, ClientDisconnect):
            return page("login", "请求无效。", 400)
        if len(values) != 1 or not 0 < len(values[0]) <= 1024:
            return page("login", "请输入节点监控密码。", 400)
        if not await access.verify(values[0]):
            return page("login", "密码不正确。", 401)
        response = RedirectResponse(PREFIX, 303, headers=headers)
        response.set_cookie(
            COOKIE_NAME,
            access.issue(),
            max_age=access.lifetime,
            path=PREFIX,
            secure=access.secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.accepts_post(request):
            return page("login", "请求来源无效。", 403)
        response = RedirectResponse(PREFIX, 303, headers=headers)
        response.delete_cookie(
            COOKIE_NAME,
            path=PREFIX,
            secure=access.secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/status")
    async def status(request: Request) -> Response:
        nonlocal cached
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.authenticated(request):
            return JSONResponse({"error": "monitor_login_required"}, 401, headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse({"error": "monitor_unavailable"}, 503, headers)
        async with refresh_lock:
            clock = asyncio.get_running_loop()
            if cached is None or clock.time() - cached[0] >= 5:
                value = await read_status(runtime)
                cached = (clock.time(), value)
        return JSONResponse(cached[1].model_dump(mode="json"), headers=headers)

    @router.get("/query-cache")
    async def query_cache(request: Request) -> Response:
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.authenticated(request):
            return JSONResponse({"error": "monitor_login_required"}, 401, headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse({"error": "monitor_unavailable"}, 503, headers)
        try:
            query = QueryCacheReportRequest.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponse({"error": "invalid_query_cache_range"}, 422, headers)
        report = await runtime.query_cache_metrics.report(
            query.hours, await runtime.query_cache.status()
        )
        return JSONResponse(report.model_dump(mode="json"), headers=headers)

    @router.post("/manage")
    async def manage(request: Request) -> Response:
        nonlocal cached
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.authenticated(request):
            return JSONResponse({"error": "请重新登录"}, 401, headers)
        if not access.accepts_post(request):
            return JSONResponse({"error": "请求来源无效"}, 403, headers)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "请提交 JSON"}, 415, headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse({"error": "节点管理暂不可用"}, 503, headers)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return JSONResponse({"error": "请求过大"}, 413, headers)
                    body.extend(chunk)
            command = NodeChange.model_validate_json(body)
            async with runtime.database.session_factory() as session:
                result = await manage_node(session, runtime.settings, command)
        except (ValidationError, ClientDisconnect, TimeoutError):
            return JSONResponse({"error": "节点 ID、公开地址或请求无效"}, 422, headers)
        except AuthFailure as exc:
            return JSONResponse({"error": exc.message}, exc.status, headers)
        except (SQLAlchemyError, OSError):
            return JSONResponse({"error": "节点清单或同步记录暂不可用"}, 503, headers)
        async with refresh_lock:
            cached = None
        return JSONResponse(result.model_dump(mode="json"), headers=headers)

    return router


def _inline_hash(document: str, tag: str) -> str:
    content = document.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
    return b64encode(hashlib.sha256(content.encode()).digest()).decode("ascii")
