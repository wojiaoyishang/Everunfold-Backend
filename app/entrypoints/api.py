"""Bootstrap ASGI application. No database, worker or business API is initialized."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.core.responses import JSONResponseError, JSONResponseFail, JSONResponseSuccess

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    application = FastAPI(
        title="Everunfold Backend",
        version="0.1.0",
        description="初始化阶段：仅提供进程活性检查，业务契约尚未发布。",
    )

    @application.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponseFail:
        # Do not expose arbitrary exception details or internal credentials.
        messages = {404: "资源不存在", 405: "请求方法不允许"}
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
        return JSONResponseError()

    @application.get("/health/live", tags=["health"])
    async def live() -> JSONResponseSuccess:
        """Report process liveness only, without claiming dependency readiness."""
        return JSONResponseSuccess(data={"status": "alive", "stage": "bootstrap"})

    return application


app = create_app()
