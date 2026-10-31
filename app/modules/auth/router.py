"""Cookie transport for request-scoped authentication use cases."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.responses import JSONResponseSuccess
from app.core.runtime import Runtime
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.schemas import AuthSessionState, LoginInput
from app.modules.auth.service import AuthService, SessionResult

router = APIRouter(
    prefix="/v1",
    tags=["authentication"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 413, 415, 422, 429, 500, 503)
    },
)


def auth_runtime(request: Request) -> Runtime:
    runtime = cast(Runtime | None, getattr(request.app.state, "runtime", None))
    if runtime is None:
        raise HTTPException(status_code=503)
    return runtime


async def auth_service(request: Request) -> AsyncIterator[AuthService]:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        yield AuthService(session, runtime.settings.auth, runtime.password_limiter)


Service = Annotated[AuthService, Depends(auth_service)]


def cookie_response(result: SessionResult, request: Request) -> JSONResponseSuccess:
    response = JSONResponseSuccess(data=result.state)
    if result.cookie is not None:
        config = auth_runtime(request).settings.auth
        response.set_cookie(
            key=config.cookie_name,
            value=result.cookie,
            path="/v1",
            domain=config.cookie_domain,
            httponly=True,
            secure=bool(config.cookie_secure),
            samesite=config.cookie_samesite,
            max_age=max(0, int((result.state.expires_at - datetime.now(UTC)).total_seconds())),
        )
    return response


def cookie(request: Request) -> str | None:
    return request.cookies.get(auth_runtime(request).settings.auth.cookie_name)


def peer(request: Request) -> str:
    # Uvicorn must run with --no-proxy-headers; forwarded headers are not peer identity.
    return request.client.host if request.client else "unknown"


@router.get("/auth/session", response_model=PublicResponse[AuthSessionState])
async def read_session(request: Request, service: Service) -> JSONResponseSuccess:
    return cookie_response(await service.read(cookie(request), peer(request)), request)


@router.post("/auth/login", response_model=PublicResponse[AuthSessionState])
async def login(payload: LoginInput, request: Request, service: Service) -> JSONResponseSuccess:
    result = await service.login(
        cookie(request),
        request.headers.get("X-CSRF-Token"),
        payload.username,
        payload.password,
        peer(request),
    )
    return cookie_response(result, request)


@router.post("/auth/logout", response_model=PublicResponse[AuthSessionState])
async def logout(request: Request, service: Service) -> JSONResponseSuccess:
    return cookie_response(
        await service.logout(cookie(request), request.headers.get("X-CSRF-Token")), request
    )


@router.get("/admin/session", response_model=PublicResponse[AuthSessionState])
async def read_admin_session(request: Request, service: Service) -> JSONResponseSuccess:
    return cookie_response(await service.read(cookie(request), peer(request), admin=True), request)
