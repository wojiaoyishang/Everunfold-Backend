"""Public reading and browser authentication with explicit lifecycle boundaries."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.core.config import ConfigurationError, Settings, load_settings
from app.core.i18n import LocaleMiddleware, i18n
from app.core.modules import (
    register_auth_module,
    register_content_module,
    register_media_module,
    register_settings_module,
    register_shortcodes_module,
)
from app.core.responses import JSONResponseError, JSONResponseFail, JSONResponseSuccess
from app.core.runtime import Runtime
from app.modules.auth.middleware import AuthBoundaryMiddleware, BusinessCORSMiddleware
from app.modules.auth.service import AuthFailure

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    i18n.load()
    resolved = settings
    if resolved is None:
        try:
            resolved = load_settings()
        except ConfigurationError:
            # Keep liveness observable; invalid configuration cannot become ready or serve data.
            logger.error("Application configuration is unavailable or invalid")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime = Runtime.create(resolved) if resolved is not None else None
        application.state.runtime = runtime
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.close()

    application = FastAPI(
        title="Everunfold Backend",
        version="0.2.0",
        description="公开阅读与浏览器认证 API；配置、显式迁移和数据准备与服务启动分离。",
        lifespan=lifespan,
    )
    application.add_middleware(
        AuthBoundaryMiddleware,
        allowed_origins=(
            [resolved.BACKEND_BASE_URL, *resolved.cors.allowed_origins] if resolved else []
        ),
    )
    application.add_middleware(
        BusinessCORSMiddleware,
        allowed_origins=resolved.cors.allowed_origins if resolved else [],
    )
    application.add_middleware(LocaleMiddleware)
    register_content_module(application)
    register_auth_module(application)
    register_settings_module(application)
    register_shortcodes_module(application)
    register_media_module(application)

    @application.exception_handler(AuthFailure)
    async def handle_auth_error(request: Request, exc: AuthFailure) -> JSONResponseFail:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return JSONResponseFail(
            status_code=exc.status, code=exc.code, msg=exc.message, headers=headers
        )

    @application.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponseFail:
        messages = {
            404: "资源不存在",
            405: "请求方法不允许",
            422: "请求参数无效",
            503: "服务暂不可用",
        }
        return JSONResponseFail(
            msg=messages.get(exc.status_code, "请求被拒绝"),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponseFail:
        return JSONResponseFail(msg="请求参数无效", status_code=422)

    @application.exception_handler(Exception)
    async def handle_server_error(request: Request, exc: Exception) -> JSONResponseError:
        # This application log omits the value; ASGI server logging still needs a
        # production redaction policy because Starlette re-raises server errors.
        logger.error("Unhandled request error: %s", type(exc).__name__)
        headers = (
            {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
            if request.url.path.startswith(("/v1/auth/", "/v1/admin/", "/v1/account/"))
            else {}
        )
        locale = getattr(request.state, "locale", "zh-CN")
        headers.update({"Content-Language": locale, "Vary": "X-Site-Locale, Accept-Language"})
        return JSONResponseError(msg=i18n.t("core.error", locale=locale), headers=headers)

    @application.exception_handler(OSError)
    @application.exception_handler(SQLAlchemyError)
    async def handle_storage_error(request: Request, exc: SQLAlchemyError) -> JSONResponseFail:
        logger.error("Request storage unavailable: %s", type(exc).__name__)
        return JSONResponseFail(msg="服务暂不可用", status_code=503)

    @application.get("/health/live", tags=["health"])
    async def live() -> JSONResponseSuccess:
        return JSONResponseSuccess(data={"status": "alive", "stage": "browser-auth"})

    @application.get("/health/ready", tags=["health"], response_model=None)
    async def ready(request: Request) -> JSONResponseSuccess | JSONResponseFail:
        runtime: Runtime | None = request.app.state.runtime
        if runtime is None:
            return JSONResponseFail(
                status_code=503,
                msg="服务暂不可用",
                data={"status": "unavailable", "checks": {"configuration": "unavailable"}},
            )
        timeout = runtime.settings.runtime.health_check_timeout_seconds

        async def database_check() -> str:
            try:
                await asyncio.wait_for(runtime.database.ping(), timeout)
                return "ok"
            except Exception:
                return "unavailable"

        async def redis_check() -> str:
            try:
                await asyncio.wait_for(runtime.redis.ping(), timeout)
                return "ok"
            except Exception:
                return "unavailable"

        async def schema_check() -> str:
            try:
                current = await asyncio.wait_for(runtime.database.schema_is_current(), timeout)
                return "ok" if current else "unavailable"
            except Exception:
                return "unavailable"

        database, redis, schema = await asyncio.gather(
            database_check(), redis_check(), schema_check()
        )
        checks = {"database": database, "redis": redis, "schema": schema}
        if all(value == "ok" for value in checks.values()):
            return JSONResponseSuccess(data={"status": "ready", "checks": checks})
        return JSONResponseFail(
            status_code=503, msg="服务暂不可用", data={"status": "unavailable", "checks": checks}
        )

    return application


app = create_app()
