"""Registration endpoints share the anonymous session and CAPTCHA boundary."""

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_service import account_mutation_lock
from app.modules.auth.registration_schemas import (
    RegistrationConfirm,
    RegistrationOptions,
    RegistrationRequest,
    RegistrationState,
)
from app.modules.auth.registration_service import RegistrationService
from app.modules.auth.router import Service, auth_runtime, cookie, cookie_response, peer
from app.modules.auth.schemas import AuthSessionState
from app.modules.captcha.service import CaptchaService, guard

router = APIRouter(
    prefix="/v1/auth/registration",
    tags=["registration"],
    responses={code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 429, 500, 503)},
)


@router.get("/options", response_model=PublicResponse[RegistrationOptions])
async def registration_options(service: Service) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await RegistrationService(service).options())


@router.post("/request", response_model=PublicResponse[RegistrationState])
async def request_registration(
    payload: RegistrationRequest, request: Request, service: Service
) -> JSONResponseSuccess:
    permit = await CaptchaService(auth_runtime(request), peer(request)).enforce(
        "registration", None, payload.captcha
    )
    await guard(service.session, permit)
    result = await RegistrationService(service).request(
        cookie(request), request.headers.get("X-CSRF-Token"), payload, peer(request)
    )
    return JSONResponseSuccess(data=result)


@router.get("/status", response_model=PublicResponse[RegistrationState])
async def registration_status(request: Request, service: Service) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await RegistrationService(service).status(cookie(request)))


@router.post("/confirm", response_model=PublicResponse[AuthSessionState])
async def confirm_registration(
    payload: RegistrationConfirm, request: Request, service: Service
) -> JSONResponseSuccess:
    await account_mutation_lock(service.session)
    result = await RegistrationService(service).confirm(
        cookie(request), request.headers.get("X-CSRF-Token"), payload
    )
    return cookie_response(result, request)
