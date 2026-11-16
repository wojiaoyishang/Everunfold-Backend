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
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.core.access_diagnostics import log_access
from app.core.query_cache_schemas import QueryCacheReportRequest
from app.core.responses import JSONResponseFail, JSONResponseSuccess
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
            log_access("nodes_monitor", "monitor_secret_invalid", request=request)
            return JSONResponseFail(
                msg="节点监控尚未配置会话密钥。", status_code=503, headers=headers
            )
        if reason := access.rejection_reason(request):
            log_access(
                "nodes_monitor",
                reason,
                request=request,
                origin=access.origin,
                trusted_tls_proxy_ips=sorted(access.trusted_tls_proxy_ips),
            )
            if (
                request.method == "GET"
                and request.url.path in {PREFIX, PREFIX + "/"}
                and request.headers.get("host", "").lower() != access.host
            ):
                return RedirectResponse(access.origin + PREFIX, 307, headers=headers)
            return JSONResponseFail(msg="资源不存在", status_code=404, headers=headers)
        return None

    def page(mode: Literal["login", "dashboard"]) -> HTMLResponse:
        return HTMLResponse(
            template.replace("{{mode}}", mode),
            200,
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
            return JSONResponseFail(msg="请求来源无效。", status_code=403, headers=headers)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return JSONResponseFail(
                msg="请使用页面上的密码表单。", status_code=415, headers=headers
            )
        peer = request.client.host if request.client else ""
        if not access.allow_attempt(peer):
            throttled = JSONResponseFail(
                msg="尝试次数过多，请稍后再试。", status_code=429, headers=headers
            )
            throttled.headers["Retry-After"] = str(LOGIN_WINDOW)
            return throttled
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return JSONResponseFail(msg="请求过大。", status_code=413, headers=headers)
                    body.extend(chunk)
            values = parse_qs(
                body.decode("ascii"),
                max_num_fields=1,
                keep_blank_values=True,
                errors="strict",
            ).get("password", [])
        except (UnicodeError, ValueError, TimeoutError, ClientDisconnect):
            return JSONResponseFail(msg="请求无效。", status_code=400, headers=headers)
        if len(values) != 1 or not 0 < len(values[0]) <= 1024:
            return JSONResponseFail(msg="请输入节点监控密码。", status_code=400, headers=headers)
        if not await access.verify(values[0]):
            return JSONResponseFail(msg="密码不正确。", status_code=401, headers=headers)
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
            return JSONResponseFail(msg="请求来源无效。", status_code=403, headers=headers)
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
            return JSONResponseFail(msg="请重新登录", status_code=401, headers=headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponseFail(msg="节点监控暂不可用", status_code=503, headers=headers)
        async with refresh_lock:
            clock = asyncio.get_running_loop()
            if cached is None or clock.time() - cached[0] >= 5:
                value = await read_status(runtime, include_pairings=True)
                cached = (clock.time(), value)
        return JSONResponseSuccess(data=cached[1], headers=headers)

    @router.get("/query-cache")
    async def query_cache(request: Request) -> Response:
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.authenticated(request):
            return JSONResponseFail(msg="请重新登录", status_code=401, headers=headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponseFail(msg="节点监控暂不可用", status_code=503, headers=headers)
        try:
            query = QueryCacheReportRequest.model_validate(dict(request.query_params))
        except ValidationError:
            return JSONResponseFail(msg="请求参数无效", status_code=422, headers=headers)
        report = await runtime.query_cache_metrics.report(
            query.hours, await runtime.query_cache.status()
        )
        return JSONResponseSuccess(data=report, headers=headers)

    @router.post("/manage")
    async def manage(request: Request) -> Response:
        nonlocal cached
        blocked = unavailable(request)
        if blocked is not None:
            return blocked
        assert access is not None
        if not access.authenticated(request):
            return JSONResponseFail(msg="请重新登录", status_code=401, headers=headers)
        if not access.accepts_post(request):
            return JSONResponseFail(msg="请求来源无效", status_code=403, headers=headers)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponseFail(msg="请提交 JSON", status_code=415, headers=headers)
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponseFail(msg="节点管理暂不可用", status_code=503, headers=headers)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return JSONResponseFail(msg="请求过大", status_code=413, headers=headers)
                    body.extend(chunk)
            command = NodeChange.model_validate_json(body)
            async with runtime.database.session_factory() as session:
                result = await manage_node(session, runtime.settings, command)
        except (ValidationError, ClientDisconnect, TimeoutError):
            return JSONResponseFail(
                msg="节点 ID、公开地址或请求无效", status_code=422, headers=headers
            )
        except AuthFailure as exc:
            return JSONResponseFail(
                msg=exc.message,
                status_code=exc.status,
                code=exc.code,
                headers={
                    **headers,
                    **(
                        {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else {}
                    ),
                },
            )
        except (SQLAlchemyError, OSError):
            return JSONResponseFail(
                msg="节点清单或同步记录暂不可用", status_code=503, headers=headers
            )
        async with refresh_lock:
            cached = None
        return JSONResponseSuccess(data=result, headers=headers)

    return router


def _inline_hash(document: str, tag: str) -> str:
    content = document.split(f"<{tag}>", 1)[1].split(f"</{tag}>", 1)[0]
    return b64encode(hashlib.sha256(content.encode()).digest()).decode("ascii")
