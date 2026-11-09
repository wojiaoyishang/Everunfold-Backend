"""No-store challenge transport and capability-protected settings."""

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.business_auth import authorize_business
from app.modules.auth.router import auth_runtime, peer
from app.modules.captcha.schemas import (
    CaptchaChallenge,
    CaptchaSettingsState,
    ChallengeRequest,
    SaveCaptchaSettings,
)
from app.modules.captcha.service import (
    CaptchaService,
    current_config,
    save_settings,
    settings_state,
)

router = APIRouter(
    tags=["captcha"],
    responses={
        status: {"model": ErrorResponse}
        for status in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
    },
)


@router.post("/v1/reader/captcha/challenge", response_model=PublicResponse[CaptchaChallenge])
async def challenge(payload: ChallengeRequest, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await CaptchaService(auth_runtime(request), peer(request)).challenge(payload)
    )


@router.get("/v1/admin/captcha", response_model=PublicResponse[CaptchaSettingsState])
async def read_settings(request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        actor, readonly = await authorize_business(request, session, runtime)
        result = settings_state(await current_config(session))
        if actor.auth_method == "api_key" and readonly:
            await session.commit()
        return JSONResponseSuccess(data=result)


@router.patch("/v1/admin/captcha", response_model=PublicResponse[CaptchaSettingsState])
async def write_settings(payload: SaveCaptchaSettings, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        actor, _ = await authorize_business(request, session, runtime)
        return JSONResponseSuccess(data=await save_settings(session, runtime, payload, actor.id))
