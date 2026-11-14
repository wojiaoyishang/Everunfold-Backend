"""Authenticated maintenance routes; portable bundles never enter a public cache."""

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.settings.router import Access, Authorized

from .health import health_report
from .schemas import (
    MaintenanceApplyRequest,
    MaintenanceBundle,
    MaintenanceExportRequest,
    MaintenanceHealth,
    MaintenanceHistory,
    MaintenanceImportRequest,
    MaintenancePreview,
    MaintenanceReceipt,
)
from .service import MaintenanceService

router = APIRouter(
    prefix="/v1/admin/maintenance",
    tags=["maintenance"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


def site_data(ctx: Access) -> MaintenanceService:
    if ctx.actor.role != "admin" or ctx.actor.auth_method != "session":
        raise AuthFailure(403, 40364, "只有管理员通过浏览器登录后才能管理站点数据。")
    return MaintenanceService(ctx.service.session, ctx.service.config, ctx.actor)


@router.get("/health", response_model=PublicResponse[MaintenanceHealth])
async def read_health(request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await health_report(auth_runtime(request), ctx.service.session))


@router.get("/history", response_model=PublicResponse[MaintenanceHistory])
async def read_history(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await site_data(ctx).history())


@router.post("/export", response_model=PublicResponse[MaintenanceBundle])
async def export_bundle(payload: MaintenanceExportRequest, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await site_data(ctx).export(payload))


@router.post("/import/preview", response_model=PublicResponse[MaintenancePreview])
async def preview_import(payload: MaintenanceImportRequest, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await site_data(ctx).preview(payload.bundle))


@router.post("/import/apply", response_model=PublicResponse[MaintenanceReceipt])
async def apply_import(payload: MaintenanceApplyRequest, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await site_data(ctx).apply(payload))
