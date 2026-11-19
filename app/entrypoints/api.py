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
from app.core.http_errors import ErrorResponseMiddleware, http_error_response
from app.core.i18n import LocaleMiddleware, i18n
from app.core.modules import (
    register_auth_module,
    register_captcha_module,
    register_content_module,
    register_maintenance_module,
    register_media_module,
    register_notifications_module,
    register_pages_module,
    register_settings_module,
    register_shortcodes_module,
)
from app.core.monitor_config import monitoring_config
from app.core.monitor_metrics import Monitoring
from app.core.plugins.business_http import BusinessHTTP, mark_error_code, mark_login_identifier
from app.core.plugins.definition import PluginRuntime
from app.core.plugins.loader import plugin_catalog
from app.core.plugins.route_runtime import mount_plugin_routes
from app.core.plugins.routes import plugin_route_policy
from app.core.process_logging import configure_process_logging
from app.core.request_ip import HTTPAccessMiddleware, RequestIPCache
from app.core.responses import JSONResponseError, JSONResponseFail, JSONResponseSuccess
from app.core.runtime import Runtime
from app.core.schemas import ErrorResponse
from app.modules.auth.middleware import AuthBoundaryMiddleware, BusinessCORSMiddleware
from app.modules.auth.service import AuthFailure
from app.modules.settings.browser_origins import ManagedBrowserOrigins
from app.modules.settings.request_ip import refresh_ip_policy
from app.plugins.nodes_monitor.plugin import mount_nodes_monitor

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    ip_policy = RequestIPCache()
    plugins = plugin_catalog()
    business = BusinessHTTP(plugins)
    options = monitoring_config()
    monitoring = Monitoring(options, plugins) if options else None
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
        refresh_task: asyncio.Task[None] | None = None
        ip_task: asyncio.Task[None] | None = None

        async def refresh_translations() -> None:
            if runtime is None:
                return
            from app.modules.settings.translation_service import refresh_translation_cache

            while True:
                try:
                    async with runtime.database.session_factory() as session:
                        await refresh_translation_cache(session)
                except Exception:
                    logger.warning(
                        "Translation cache refresh unavailable; built-in messages remain active"
                    )
                await asyncio.sleep(30)

        if runtime is not None:
            refresh_task = asyncio.create_task(refresh_translations())
            ip_task = asyncio.create_task(refresh_ip_policy(runtime, ip_policy))
        try:
            async with (
                plugins.lifespan(PluginRuntime(role="api", runtime=runtime)),
                business.lifespan(runtime),
            ):
                collection = (
                    asyncio.create_task(monitoring.refresh(runtime))
                    if monitoring and runtime is not None
                    else None
                )
                try:
                    yield
                finally:
                    if collection is not None:
                        collection.cancel()
                        await asyncio.gather(collection, return_exceptions=True)
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                await asyncio.gather(refresh_task, return_exceptions=True)
            if ip_task is not None:
                ip_task.cancel()
                await asyncio.gather(ip_task, return_exceptions=True)
            if runtime is not None:
                await runtime.close()

    application = FastAPI(
        title="Everunfold Backend",
        version="0.2.0",
        description="公开阅读与浏览器认证 API；配置、显式迁移和数据准备与服务启动分离。",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={
            status: {"model": ErrorResponse}
            for status in (400, 401, 403, 404, 405, 413, 415, 422, 429, 500, 503)
        },
    )
    application.add_middleware(
        AuthBoundaryMiddleware,
        allowed_origins=(
            [
                origin
                for origin in (resolved.BACKEND_BASE_URL, *resolved.cors.allowed_origins)
                if origin
            ]
            if resolved
            else []
        ),
    )
    application.add_middleware(
        BusinessCORSMiddleware,
        allowed_origins=resolved.cors.allowed_origins if resolved else [],
    )
    if resolved and resolved.cors.use_frontend_registry:
        application.add_middleware(ManagedBrowserOrigins)
    application.add_middleware(LocaleMiddleware)
    application.add_middleware(ErrorResponseMiddleware)
    register_content_module(application)
    register_auth_module(application)
    register_captcha_module(application)
    register_settings_module(application)
    register_shortcodes_module(application)
    register_notifications_module(application)
    register_pages_module(application)
    register_media_module(application)
    register_maintenance_module(application)
    mount_nodes_monitor(application, resolved)
    if monitoring:
        monitoring.install(application)
    application.state.ip_policy = ip_policy
    application.add_middleware(HTTPAccessMiddleware, policy=ip_policy, business=business)

    @application.exception_handler(AuthFailure)
    async def handle_auth_error(request: Request, exc: AuthFailure) -> JSONResponseFail:
        mark_error_code(exc.code)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return JSONResponseFail(
            status_code=exc.status, code=exc.code, msg=exc.message, headers=headers
        )

    @application.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponseFail:
        return http_error_response(exc.status_code, exc.headers)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponseFail:
        if isinstance(exc.body, dict):
            mark_login_identifier(exc.body.get("username"), password_only=True)
        return JSONResponseFail(msg="请求参数无效", status_code=422)

    @application.exception_handler(Exception)
    async def handle_server_error(request: Request, exc: Exception) -> JSONResponseError:
        # This application log omits the value; ASGI server logging still needs a
        # production redaction policy because Starlette re-raises server errors.
        logger.error("Unhandled request error: %s", type(exc).__name__)
        headers = (
            {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
            if plugin_route_policy(request.scope, request.method) is not None
            or request.url.path.startswith(
                (
                    "/v1/auth/",
                    "/v1/admin/",
                    "/v1/account/",
                    "/v1/reader/",
                    "/v1/plugins/",
                    "/_nodes",
                )
            )
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

    @application.get("/", include_in_schema=False)
    async def index() -> JSONResponseSuccess:
        return JSONResponseSuccess(
            msg="当我离去的时候，让我的思想来到你的身边，正如那夕阳的余晖，映在寂静星空的边缘。"
        )

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

    # Give the host's routes their normal matching priority over plugin registrations.
    mount_plugin_routes(application, plugins)
    return application


configure_process_logging("API", "API_LOG_LEVEL")
app = create_app()
