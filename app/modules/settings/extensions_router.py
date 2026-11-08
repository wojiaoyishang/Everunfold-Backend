"""Node inventories require real socket IP AND the node's private deployment token."""

import hmac
import ipaddress
import os

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.settings.extension_usage import installed_plugins, scan_usage
from app.modules.settings.extensions_schemas import (
    ExtensionCatalog,
    ExtensionInventory,
    ExtensionUsage,
    ExtensionUsageQuery,
)
from app.modules.settings.extensions_service import catalog, install_inventory
from app.modules.settings.router import Authorized

router = APIRouter(
    tags=["extensions"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.get("/v1/admin/extensions", response_model=PublicResponse[ExtensionCatalog])
async def read_extensions(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await catalog(ctx.service.session, ctx.service.config))


@router.post("/v1/admin/extensions/usage", response_model=PublicResponse[ExtensionUsage])
async def check_extension_usage(
    payload: ExtensionUsageQuery, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await scan_usage(
            ctx.service.session, await installed_plugins(ctx.service.session), payload.plugin_ids
        )
    )


@router.post("/internal/extensions/{node_id}")
async def report_extensions(node_id: str, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    config = runtime.settings
    node = config.frontends.get(node_id)
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
        allowed = any(
            address in ipaddress.ip_network(network, strict=False)
            for network in config.internal_auth.allowed_peer_ips
        )
    except ValueError:
        allowed = False
    token = os.environ.get(node.nextjs_to_fastapi_token_env, "") if node else ""
    supplied = request.headers.get("authorization", "")
    if (
        not allowed
        or not node
        or not node.enabled
        or not token
        or not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode())
    ):
        raise AuthFailure(403, 40320, "内部请求未通过验证")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 512 * 1024:
            raise AuthFailure(413, 41320, "扩展清单过大")
    try:
        value = ExtensionInventory.model_validate_json(data)
    except ValidationError as exc:
        raise AuthFailure(422, 42298, "扩展清单无效") from exc
    async with runtime.database.session_factory() as session:
        await install_inventory(session, config, node_id, value)
    return JSONResponseSuccess(data={"node_id": node_id, "registered": True})
