"""Browser-session-only access policy management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import AccountAccess, Authorized
from app.modules.auth.accounts_schemas import AccessSettingsInput, AccessSettingsState
from app.modules.auth.permissions import user_permissions
from app.modules.auth.registration_schemas import (
    InvitationBatchCreate,
    InvitationBatchCreated,
    InvitationCreate,
    InvitationCreated,
    InvitationList,
    InvitationRevoke,
)
from app.modules.auth.registration_service import RegistrationService
from app.modules.auth.router import auth_runtime
from app.modules.auth.security_settings import (
    read_access_settings,
    save_access_settings,
)
from app.modules.auth.service import AuthFailure

router = APIRouter(
    prefix="/v1/admin/access",
    tags=["access"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 500, 503)},
)


def require_access_manager(ctx: Authorized) -> AccountAccess:
    if user_permissions(ctx.actor).get("access:manage") != "all":
        raise AuthFailure(403, 40374, "没有管理访问与安全的权限")
    return ctx


Manager = Annotated[AccountAccess, Depends(require_access_manager)]


@router.get("/settings", response_model=PublicResponse[AccessSettingsState])
async def get_settings(ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await read_access_settings(ctx.service.session))


@router.patch("/settings", response_model=PublicResponse[AccessSettingsState])
async def set_settings(
    payload: AccessSettingsInput, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await save_access_settings(
            ctx.service.session, auth_runtime(request), ctx.actor.id, payload
        )
    )


@router.get("/invitations", response_model=PublicResponse[InvitationList])
async def list_invitations(ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await RegistrationService(ctx.service.auth).invitations())


@router.post("/invitations", response_model=PublicResponse[InvitationCreated])
async def create_invitation(payload: InvitationCreate, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await RegistrationService(ctx.service.auth).invite(ctx.actor, payload.days_valid)
    )


@router.post("/invitations/batch", response_model=PublicResponse[InvitationBatchCreated])
async def create_invitation_batch(
    payload: InvitationBatchCreate, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await RegistrationService(ctx.service.auth).invite_batch(
            ctx.actor, payload.count, payload.expires_at
        )
    )


@router.post("/invitations/revoke", response_model=PublicResponse[InvitationList])
async def revoke_invitation(payload: InvitationRevoke, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await RegistrationService(ctx.service.auth).revoke(ctx.actor, payload.id)
    )
