"""Private key/application management plus independently signed service introspection."""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from redis.exceptions import RedisError

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import AccountAccess, Authorized
from app.modules.auth.accounts_router import Manager as UserManager
from app.modules.auth.application_service import ApplicationService
from app.modules.auth.application_signatures import (
    WINDOW_SECONDS,
    application_secret,
    key_id,
    signed_headers,
    verify_signature,
)
from app.modules.auth.credentials_models import ApiApplication
from app.modules.auth.credentials_schemas import (
    ApplicationCreate,
    ApplicationList,
    ApplicationMutation,
    ApplicationRotate,
    ApplicationSave,
    ApplicationState,
    CallbackCommand,
    IntrospectionInput,
    IntrospectionState,
    KeyCreate,
    KeyList,
    KeyMutation,
    KeyOptions,
    KeyQuery,
    KeyRevoke,
)
from app.modules.auth.key_service import KeyService, introspect_key
from app.modules.auth.permissions import user_permissions
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure

router = APIRouter(
    prefix="/v1",
    tags=["api-keys"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)
    },
)


async def application_manager(ctx: Authorized) -> AccountAccess:
    if user_permissions(ctx.actor).get("applications:manage") != "all":
        raise AuthFailure(403, 40360, "没有管理接入应用的权限")
    return ctx


Manager = Annotated[AccountAccess, Depends(application_manager)]


def applications(request: Request, ctx: AccountAccess) -> ApplicationService:
    return ApplicationService(ctx.service, auth_runtime(request).settings)


@router.get("/account/api-keys/options", response_model=PublicResponse[KeyOptions])
async def key_options(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await KeyService(ctx.service).options(ctx.actor))


@router.get("/account/api-keys", response_model=PublicResponse[KeyList])
async def keys(ctx: Authorized, filters: Annotated[KeyQuery, Query()]) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await KeyService(ctx.service).list_keys(ctx.actor, filters))


@router.get("/admin/users/{user_id}/api-keys", response_model=PublicResponse[KeyList])
async def user_keys(
    user_id: UUID, ctx: UserManager, filters: Annotated[KeyQuery, Query()]
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await KeyService(ctx.service).list_keys(await ctx.service.target(user_id), filters)
    )


@router.post("/admin/users/{user_id}/api-keys/revoke", response_model=PublicResponse[KeyList])
async def revoke_user_key(
    user_id: UUID, payload: KeyRevoke, ctx: UserManager
) -> JSONResponseSuccess:
    owner = await ctx.service.target(user_id)
    if owner.role == "admin" and ctx.actor.role != "admin":
        raise AuthFailure(403, 40360, "不能修改管理员账号")
    return JSONResponseSuccess(
        data=await KeyService(ctx.service).revoke(ctx.actor, payload, owner=owner)
    )


@router.post("/account/api-keys", response_model=PublicResponse[KeyMutation], status_code=201)
async def create_key(payload: KeyCreate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await KeyService(ctx.service).create(ctx.actor, payload), status_code=201
    )


@router.post("/account/api-keys/revoke", response_model=PublicResponse[KeyList])
async def revoke_key(payload: KeyRevoke, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await KeyService(ctx.service).revoke(ctx.actor, payload))


@router.get("/admin/api-key-applications", response_model=PublicResponse[ApplicationList])
async def list_applications(request: Request, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await applications(request, ctx).list())


@router.post(
    "/admin/api-key-applications",
    response_model=PublicResponse[ApplicationMutation],
    status_code=201,
)
async def create_application(
    payload: ApplicationCreate, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await applications(request, ctx).create(ctx.actor, payload), status_code=201
    )


@router.patch(
    "/admin/api-key-applications/{app_id}", response_model=PublicResponse[ApplicationMutation]
)
async def save_application(
    app_id: str, payload: ApplicationSave, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await applications(request, ctx).save(ctx.actor, app_id, payload)
    )


@router.post(
    "/admin/api-key-applications/{app_id}/credentials",
    response_model=PublicResponse[ApplicationMutation],
)
async def rotate_application(
    app_id: str, payload: ApplicationRotate, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await applications(request, ctx).rotate(ctx.actor, app_id, payload)
    )


@router.post(
    "/admin/api-key-applications/{app_id}/callback", response_model=PublicResponse[ApplicationState]
)
async def command_callback(
    app_id: str, payload: CallbackCommand, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await applications(request, ctx).command(ctx.actor, app_id, payload)
    )


@router.post("/auth/api-keys/introspect", response_model=PublicResponse[IntrospectionState])
async def introspect(payload: IntrospectionInput, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        app = await session.get(ApiApplication, request.headers.get("X-Everunfold-App", ""))
        if app is None or not app.active or app.query_key_version < 1:
            raise AuthFailure(401, 40160, "应用请求签名无效或已过期")
        if request.url.query:
            raise AuthFailure(401, 40160, "应用请求签名无效或已过期")
        target = runtime.settings.BACKEND_BASE_URL.rstrip("/") + "/v1/auth/api-keys/introspect"
        kid = key_id(app, "query") or ""
        secret = application_secret(runtime.settings.auth, app.id, app.query_key_version, "query")
        nonce = verify_signature(
            secret, app.id, kid, "POST", target, await request.body(), request.headers
        )
        replay_key = (
            runtime.settings.redis_prefix
            + "auth:app-nonce:"
            + hashlib.sha256((kid + ":" + nonce).encode()).hexdigest()
        )
        try:
            fresh = await runtime.redis.set(replay_key, "1", nx=True, ex=WINDOW_SECONDS * 2)
        except RedisError as error:
            raise AuthFailure(503, 50360, "应用校验服务暂时不可用") from error
        if not fresh:
            raise AuthFailure(401, 40160, "应用请求签名无效或已过期")
        result = await introspect_key(session, app, runtime.settings.auth, payload)
        response = JSONResponseSuccess(data=result.model_dump(mode="json", exclude_none=True))
        signature = signed_headers(
            secret, app.id, kid, "RESPONSE", target, bytes(response.body), nonce=nonce
        )
        for name, value in signature.items():
            if name.lower() != "content-type":
                response.headers[name] = value
        return response
