"""Session-only account surfaces; machine credentials cannot manage account security."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.core.i18n import i18n
from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_schemas import (
    AccountProfile,
    AccountUserList,
    AccountUserQuery,
    EmailVerificationConfirm,
    EmailVerificationRequest,
    EmailVerificationState,
    PasswordChange,
    PasswordReset,
    PermissionCatalog,
    ProfileSave,
    ReviewAccountEmail,
    SessionList,
    SessionRevoke,
    UserCreate,
    UserSave,
)
from app.modules.auth.accounts_service import AccountsService, account_mutation_lock, profile
from app.modules.auth.credential_policy import application_scopes, owner_permissions
from app.modules.auth.credentials_models import ApiApplication
from app.modules.auth.credentials_schemas import KeyApplication
from app.modules.auth.email_verification_service import EmailVerificationService
from app.modules.auth.models import User
from app.modules.auth.permission_schemas import PermissionDefinition
from app.modules.auth.permissions import CAPABILITIES, user_permissions
from app.modules.auth.router import auth_runtime, cookie, cookie_response, peer
from app.modules.auth.schemas import AuthSessionState
from app.modules.auth.service import AuthFailure, AuthService
from app.modules.auth.two_factor_schemas import (
    TwoFactorActivated,
    TwoFactorBegin,
    TwoFactorChange,
    TwoFactorCode,
    TwoFactorSetup,
    TwoFactorState,
)
from app.modules.settings.languages import LanguageCatalog, language_catalog

router = APIRouter(
    prefix="/v1",
    tags=["accounts"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)
    },
)


@dataclass
class AccountAccess:
    service: AccountsService
    actor: User
    token: str


async def account_access(request: Request) -> AsyncIterator[AccountAccess]:
    runtime = auth_runtime(request)
    if request.headers.get("Authorization") is not None:
        raise AuthFailure(403, 40362, "此操作需要登录会话")
    async with runtime.database.session_factory() as session:
        write = request.method not in {"GET", "HEAD", "OPTIONS"}
        if write and request.url.path != "/v1/account/avatars/upload":
            await account_mutation_lock(session)
        auth = AuthService(session, runtime.settings.auth, runtime.password_limiter)
        actor = await auth.require_user(
            cookie(request), request.headers.get("X-CSRF-Token"), write=write
        )
        yield AccountAccess(AccountsService(session, auth), actor, cookie(request) or "")


Authorized = Annotated[AccountAccess, Depends(account_access)]


async def user_manager(ctx: Authorized) -> AccountAccess:
    if user_permissions(ctx.actor).get("users:manage") != "all":
        raise AuthFailure(403, 40360, "没有管理用户的权限")
    return ctx


Manager = Annotated[AccountAccess, Depends(user_manager)]


@router.get("/account/two-factor", response_model=PublicResponse[TwoFactorState])
async def two_factor_state(ctx: Authorized) -> JSONResponseSuccess:
    from app.modules.auth.two_factor_service import TwoFactorService

    return JSONResponseSuccess(data=await TwoFactorService(ctx.service.auth).state(ctx.actor))


@router.post("/account/two-factor/setup", response_model=PublicResponse[TwoFactorSetup])
async def setup_two_factor(payload: TwoFactorBegin, ctx: Authorized) -> JSONResponseSuccess:
    from app.modules.auth.two_factor_service import TwoFactorService

    return JSONResponseSuccess(
        data=await TwoFactorService(ctx.service.auth).begin(ctx.actor, payload.current_password)
    )


@router.post("/account/two-factor/activate", response_model=PublicResponse[TwoFactorActivated])
async def activate_two_factor(payload: TwoFactorCode, ctx: Authorized) -> JSONResponseSuccess:
    from app.modules.auth.two_factor_service import TwoFactorService

    return JSONResponseSuccess(
        data=await TwoFactorService(ctx.service.auth).activate(ctx.actor, payload.code, ctx.token)
    )


@router.post("/account/two-factor/disable", response_model=PublicResponse[TwoFactorState])
async def disable_two_factor(payload: TwoFactorChange, ctx: Authorized) -> JSONResponseSuccess:
    from app.modules.auth.two_factor_service import TwoFactorService

    return JSONResponseSuccess(
        data=await TwoFactorService(ctx.service.auth).change(
            ctx.actor, payload.current_password, payload.code, ctx.token, disable=True
        )
    )


@router.post(
    "/account/two-factor/recovery-codes", response_model=PublicResponse[TwoFactorActivated]
)
async def regenerate_recovery_codes(
    payload: TwoFactorChange, ctx: Authorized
) -> JSONResponseSuccess:
    from app.modules.auth.two_factor_service import TwoFactorService

    return JSONResponseSuccess(
        data=await TwoFactorService(ctx.service.auth).change(
            ctx.actor, payload.current_password, payload.code, ctx.token, disable=False
        )
    )


@router.get("/account/languages", response_model=PublicResponse[LanguageCatalog])
async def account_languages(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await language_catalog(ctx.service.session))


@router.get("/account/profile", response_model=PublicResponse[AccountProfile])
async def own_profile(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.read_profile(ctx.actor))


@router.patch("/account/profile", response_model=PublicResponse[AccountProfile])
async def save_profile(payload: ProfileSave, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.save_profile(ctx.actor, payload))


@router.post(
    "/account/email-verification/request",
    response_model=PublicResponse[EmailVerificationState],
)
async def request_email_verification(
    payload: EmailVerificationRequest, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    from app.modules.captcha.service import CaptchaService, guard

    service = EmailVerificationService(ctx.service.session)
    if existing := await service.replay(ctx.actor, payload):
        return JSONResponseSuccess(data=existing)
    permit = await CaptchaService(auth_runtime(request), peer(request)).enforce(
        "email_verification", None, payload.captcha
    )
    await guard(ctx.service.session, permit)
    return JSONResponseSuccess(data=await service.request(ctx.actor, payload))


@router.get(
    "/account/email-verification/{request_id}",
    response_model=PublicResponse[EmailVerificationState],
)
async def email_verification_status(request_id: UUID, ctx: Authorized) -> JSONResponseSuccess:
    service = EmailVerificationService(ctx.service.session)
    return JSONResponseSuccess(data=await service.status(ctx.actor, request_id))


@router.post(
    "/account/email-verification/confirm",
    response_model=PublicResponse[AccountProfile],
)
async def confirm_email_verification(
    payload: EmailVerificationConfirm, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await EmailVerificationService(ctx.service.session).confirm(ctx.actor, payload)
    )


@router.post("/account/password", response_model=PublicResponse[AuthSessionState])
async def change_password(
    payload: PasswordChange, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    return cookie_response(
        await ctx.service.change_password(ctx.actor, ctx.token, payload), request
    )


@router.get("/account/sessions", response_model=PublicResponse[SessionList])
async def own_sessions(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.sessions(ctx.actor, ctx.token))


@router.post("/account/sessions/revoke", response_model=PublicResponse[SessionList])
async def revoke_session(payload: SessionRevoke, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.revoke_session(ctx.actor, ctx.token, payload))


@router.get("/admin/users", response_model=PublicResponse[AccountUserList])
async def list_users(
    ctx: Manager,
    filters: Annotated[AccountUserQuery, Query()],
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ctx.service.list_users(
            filters.q, filters.page, filters.page_size, filters.active
        )
    )


@router.get("/admin/users/{user_id}", response_model=PublicResponse[AccountProfile])
async def get_user(user_id: UUID, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=profile(await ctx.service.target(user_id)))


@router.post("/admin/users", response_model=PublicResponse[AccountProfile], status_code=201)
async def create_user(payload: UserCreate, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ctx.service.create_user(ctx.actor, payload), status_code=201
    )


@router.patch("/admin/users/{user_id}", response_model=PublicResponse[AccountProfile])
async def save_user(user_id: UUID, payload: UserSave, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.save_user(ctx.actor, user_id, payload))


@router.post(
    "/admin/users/{user_id}/email-verification", response_model=PublicResponse[AccountProfile]
)
async def review_user_email(
    user_id: UUID, payload: ReviewAccountEmail, ctx: Manager
) -> JSONResponseSuccess:
    from app.modules.auth.email_review import review_email

    return JSONResponseSuccess(data=await review_email(ctx.service, ctx.actor, user_id, payload))


@router.post("/admin/users/{user_id}/password", response_model=PublicResponse[AccountProfile])
async def reset_password(
    user_id: UUID, payload: PasswordReset, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.reset_password(ctx.actor, user_id, payload))


@router.get("/account/permissions", response_model=PublicResponse[PermissionCatalog])
async def permissions(ctx: Authorized) -> JSONResponseSuccess:
    applications = []
    for app in await ctx.service.session.scalars(
        select(ApiApplication).where(ApiApplication.active.is_(True))
    ):
        if app.audience == app.id and app.id != "everunfold":
            available = owner_permissions(ctx.actor, app)
            if available:
                applications.append(
                    KeyApplication(
                        id=app.id,
                        name=app.name,
                        audience=app.audience,
                        permissions=available,
                        scopes=application_scopes(app),
                    )
                )
    return JSONResponseSuccess(
        data=PermissionCatalog(
            items=[
                PermissionDefinition(
                    code=code, label=i18n.message(label), api_key=key, owner_scope=owner
                )
                for code, (label, key, owner) in CAPABILITIES.items()
            ],
            applications=applications,
        )
    )
