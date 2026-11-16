"""Node inventories require real socket IP AND the node's private deployment token."""

import hmac
import ipaddress
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_diagnostics import AccessBoundary, log_access
from app.core.config import FrontendConfig, Settings, is_loopback_host
from app.core.plugins.loader import plugin_catalog
from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.extension_usage import installed_plugins, scan_usage
from app.modules.settings.extensions_schemas import (
    ExtensionCatalog,
    ExtensionInventory,
    ExtensionUsage,
    ExtensionUsageQuery,
)
from app.modules.settings.extensions_service import catalog, install_inventory
from app.modules.settings.manual_sync import stage_node_sync
from app.modules.settings.node_registry import NodeRegistration, read_targets
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


async def authorize_node(
    node_id: str,
    request: Request,
    session: AsyncSession,
    config: Settings,
    boundary: AccessBoundary,
) -> FrontendConfig:
    def denied(code: str) -> AuthFailure:
        log_access(
            boundary,
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
    return node


@router.post("/internal/frontend-nodes/{node_id}/sync", include_in_schema=False)
async def request_node_sync(node_id: str, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    config = runtime.settings
    async with runtime.database.session_factory() as session:
        await authorize_node(node_id, request, session, config, "internal_node_sync")
        await lock_references(session)
        # Node registration and pairing can change before we acquire the lock.
        await authorize_node(node_id, request, session, config, "internal_node_sync")
        await stage_node_sync(session, config, node_id, actor="frontend_node")
        await session.commit()
    return JSONResponseSuccess(data={"node_id": node_id, "queued": True})


@router.post("/internal/extensions/{node_id}")
async def report_extensions(node_id: str, request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    config = runtime.settings
    async with runtime.database.session_factory() as session:
        node = await authorize_node(node_id, request, session, config, "internal_extensions")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 512 * 1024:
            raise AuthFailure(413, 41320, "扩展清单过大")
    try:
        value = ExtensionInventory.model_validate_json(data)
        public_url = request.headers.get("x-everunfold-public-url")
        if public_url is not None:
            public_url = NodeRegistration(base_url=public_url, enabled=True).base_url
            parsed = urlsplit(public_url)
            if public_url and (
                parsed.port == 0
                or (parsed.scheme != "https" and not is_loopback_host(parsed.hostname or ""))
            ):
                raise ValueError("invalid published origin")
    except ValueError as exc:
        raise AuthFailure(422, 42298, "扩展清单或前端发布 URL 无效") from exc
    async with runtime.database.session_factory() as session:
        await install_inventory(
            session, config, node_id, value, authorized_target=node, public_url=public_url
        )
    return JSONResponseSuccess(data={"node_id": node_id, "registered": True})
