"""Public self-service password recovery uses the same anonymous CSRF session as login."""

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_service import account_mutation_lock
from app.modules.auth.recovery_schemas import (
    RecoveryAccepted,
    RecoveryConfirm,
    RecoveryOptions,
    RecoveryRequest,
)
from app.modules.auth.recovery_service import RecoveryService
from app.modules.auth.router import Service, cookie, peer
from app.modules.auth.security_settings import recovery_policy
from app.modules.content.models import SiteSettings

router = APIRouter(
    prefix="/v1/auth/password-recovery",
    tags=["password recovery"],
    responses={code: {"model": ErrorResponse} for code in (403, 409, 422, 429, 503)},
)


@router.get("/options", response_model=PublicResponse[RecoveryOptions])
async def recovery_options(service: Service) -> JSONResponseSuccess:
    site = await service.session.get(SiteSettings, 1)
    enabled, _ = recovery_policy(site.data if site else {})
    return JSONResponseSuccess(data=RecoveryOptions(enabled=enabled))


@router.post("/request", response_model=PublicResponse[RecoveryAccepted])
async def request_recovery(
    payload: RecoveryRequest, request: Request, service: Service
) -> JSONResponseSuccess:
    await RecoveryService(service).request(
        cookie(request), request.headers.get("X-CSRF-Token"), payload, peer(request)
    )
    return JSONResponseSuccess(data=RecoveryAccepted())


@router.post("/confirm", response_model=PublicResponse[RecoveryAccepted])
async def confirm_recovery(
    payload: RecoveryConfirm, request: Request, service: Service
) -> JSONResponseSuccess:
    await account_mutation_lock(service.session)
    await RecoveryService(service).confirm(
        cookie(request), request.headers.get("X-CSRF-Token"), payload, peer(request)
    )
    return JSONResponseSuccess(data=RecoveryAccepted())
