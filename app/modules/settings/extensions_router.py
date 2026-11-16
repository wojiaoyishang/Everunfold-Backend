"""Node inventories require real socket IP AND the node's private deployment token."""

import hmac
import ipaddress
import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from app.core.access_diagnostics import log_access
from app.core.plugins.loader import plugin_catalog
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
from app.modules.settings.node_registry import read_targets
from app.modules.settings.router import Authorized

router = APIRouter(
    tags=["extensions"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.get("/v1/extensions/artwork/{plugin_id}/{filename}", include_in_schema=False)
async def plugin_artwork(plugin_id: str, filename: str) -> Response:
    for item in plugin_catalog().plugins:
        artwork = item.artwork
        if item.info.id == plugin_id and artwork is not None and artwork.filename == filename:
            return Response(
                content=artwork.body,
                media_type=artwork.media_type,
                headers={
                    "Cache-Control": "public, max-age=31536000, immutable",
                    "X-Content-Type-Options": "nosniff",
                },
            )
    raise HTTPException(status_code=404)


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

    def denied(code: str) -> AuthFailure:
        log_access(
            "internal_extensions",
            code,
            request=request,
            node_id=node_id,
            allowed_peer_ips=config.internal_auth.allowed_peer_ips,
        )
        return AuthFailure(403, 40320, "内部请求未通过验证")

    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
        allowed = any(
            address in ipaddress.ip_network(network, strict=False)
            for network in config.internal_auth.allowed_peer_ips
        )
    except ValueError:
        allowed = False
    supplied = request.headers.get("authorization", "")
    if not allowed:
        raise denied("peer_not_allowed")
    if not supplied.startswith("Bearer "):
        raise denied("token_missing")
    async with runtime.database.session_factory() as session:
        node = (await read_targets(session, config)).get(node_id)
    if not node:
        raise denied("node_not_registered")
    if not node.enabled:
        raise denied("node_disabled")
    token = os.environ.get(node.nextjs_to_fastapi_token_env, "")
    if not token:
        raise denied("token_unconfigured")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise denied("token_mismatch")
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
        await install_inventory(session, config, node_id, value, authorized_target=node)
    return JSONResponseSuccess(data={"node_id": node_id, "registered": True})
