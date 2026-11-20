"""Admin access to a node's real disk state and cache maintenance."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx2 as httpx
from fastapi import APIRouter, Query, Request
from pydantic import ValidationError

from app.core.query_cache_schemas import QueryCacheReport, QueryCacheReportRequest
from app.core.responses import JSONResponseSuccess
from app.core.runtime import Runtime
from app.core.schemas import ErrorResponse, PublicModel, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.cache_schemas import (
    CacheClearReceipt,
    CacheDeletionReceipt,
    CacheNodeState,
    CachePruneReceipt,
    CacheRebuild,
    CacheRebuildResult,
    CacheReceiverStatus,
    CacheReceiverTarget,
)
from app.modules.settings.models import SettingsAudit
from app.modules.settings.node_registry import NodeBinding, NodeId, read_directory
from app.modules.settings.nodes_schemas import NodeStatus, ReceiverProbe
from app.modules.settings.router import Authorized
from app.modules.settings.sync_status import read_history

router = APIRouter(
    prefix="/v1/admin/frontend-nodes",
    tags=["cache"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 500, 502, 503)},
)

ReceiverError = Literal[
    "disabled",
    "not_configured",
    "credential_missing",
    "unreachable",
    "rejected",
    "unsupported",
    "invalid",
    "identity_mismatch",
]


class ReceiverFailure(Exception):
    def __init__(self, state: ReceiverError) -> None:
        self.state = state


async def receiver_request[Result: PublicModel](
    runtime: Runtime,
    target: NodeBinding | None,
    identity: CacheReceiverTarget,
    response_type: type[Result],
    *,
    operation: Literal["status", "prune", "clear"] = "status",
) -> Result:
    if target is None or not target.settings_receiver_url:
        raise ReceiverFailure("not_configured")
    if not target.enabled:
        raise ReceiverFailure("disabled")
    secret = target.token(runtime.settings, identity.node_id, "outbound")
    if len(secret) < 32:
        raise ReceiverFailure("credential_missing")
    url = target.settings_receiver_url + f"/internal/cache/v1/{operation}"
    timeout = {"clear": 30, "prune": 8}.get(
        operation, runtime.settings.runtime.health_check_timeout_seconds
    )
    write = operation != "status"
    try:
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout) as client,
            client.stream(
                "POST" if write else "GET",
                url,
                headers={"Authorization": "Bearer " + secret, "Accept-Encoding": "identity"},
                json=identity.model_dump(mode="json") if write else None,
            ) as response,
        ):
            if response.status_code in {401, 403}:
                raise ReceiverFailure("rejected")
            if response.status_code in {404, 405}:
                raise ReceiverFailure("unsupported")
            if response.status_code == 409:
                raise ReceiverFailure("identity_mismatch")
            if (
                response.status_code != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ReceiverFailure("invalid")
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > 8192:
                    raise ReceiverFailure("invalid")
        report = response_type.model_validate_json(body)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ReceiverFailure("unreachable") from exc
    except ValidationError as exc:
        raise ReceiverFailure("invalid") from exc
    data = report.model_dump()
    if data["node_id"] != identity.node_id or data["source_epoch"] != identity.source_epoch:
        raise ReceiverFailure("identity_mismatch")
    return report


async def cache_node(node_id: str, ctx: Authorized) -> tuple[NodeStatus, NodeBinding | None]:
    directory = await read_directory(ctx.service.session, ctx.service.config)
    entry = directory.entries.get(node_id)
    if entry is None:
        raise AuthFailure(404, 40420, "节点不存在")
    target = directory.targets.get(node_id)
    return NodeStatus(
        id=node_id,
        enabled=entry.enabled,
        base_url=entry.base_url,
        receiver_configured=bool(target and target.settings_receiver_url),
        receiver=ReceiverProbe(state="unavailable"),
    ), target


@router.get("/query-cache", response_model=PublicResponse[QueryCacheReport])
async def read_query_cache(
    request: Request, ctx: Authorized, query: Annotated[QueryCacheReportRequest, Query()]
) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    report = await runtime.query_cache_metrics.report(
        query.hours, await runtime.query_cache.status()
    )
    return JSONResponseSuccess(data=report, headers={"Cache-Control": "private, no-store"})


@router.get("/{node_id}/cache", response_model=PublicResponse[CacheNodeState])
async def read_cache(node_id: NodeId, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    node, target = await cache_node(node_id, ctx)
    checked = datetime.now(UTC)
    epoch = await read_history(
        ctx.service.session, [node], checked, runtime.settings.outbox.max_attempts
    )
    await ctx.service.session.commit()
    storage = None
    state: Literal["ready"] | ReceiverError = "ready"
    if not node.enabled:
        state = "disabled"
    elif epoch is None:
        raise AuthFailure(503, 50320, "站点设置尚未初始化")
    else:
        try:
            storage = await receiver_request(
                runtime,
                target,
                CacheReceiverTarget(node_id=node_id, source_epoch=str(epoch)),
                CacheReceiverStatus,
            )
        except ReceiverFailure as exc:
            state = exc.state
    return JSONResponseSuccess(
        data=CacheNodeState(
            node_id=node_id,
            checked_at=checked,
            receiver=state,
            storage=storage,
            sync=node.sync,
        )
    )


@router.post("/{node_id}/cache/rebuild", response_model=PublicResponse[CacheRebuildResult])
async def rebuild_cache(
    node_id: NodeId, payload: CacheRebuild, ctx: Authorized
) -> JSONResponseSuccess:
    service = ctx.service
    await lock_references(service.session)
    site = await service.site(locked=True)
    node, _ = await cache_node(node_id, ctx)
    if not node.enabled or not node.receiver_configured:
        raise AuthFailure(409, 40925, "请先启用节点并绑定同步接收器")
    if payload.scope != "public-content":
        await service.stage_retry(node_id, ctx.actor.id, scope=payload.scope)
    if payload.scope in {"all", "public-content"}:
        await ContentPublication(service.session, service.config).stage_retry(
            ctx.actor.id, node_id=node_id
        )
    service.session.add(
        SettingsAudit(
            user_id=ctx.actor.id,
            resource=f"cache:{node_id}",
            revision=site.content_generation,
            changes={"action": "rebuild", "scope": payload.scope},
        )
    )
    await service.session.commit()
    return JSONResponseSuccess(data=CacheRebuildResult(queued=True))


async def delete_cache(
    node_id: NodeId,
    request: Request,
    ctx: Authorized,
    operation: Literal["prune", "clear"],
    response_type: type[CacheDeletionReceipt],
) -> JSONResponseSuccess:
    service = ctx.service
    site = await service.site()
    node, target = await cache_node(node_id, ctx)
    if not node.enabled or not node.receiver_configured:
        raise AuthFailure(409, 40925, "请先启用节点并绑定同步接收器")
    epoch, revision, actor = site.source_epoch, site.content_generation, ctx.actor.id
    # Record the authorized operation before the disk side effect. No SQL locks span HTTP.
    audit = SettingsAudit(
        user_id=actor,
        resource=f"cache:{node_id}",
        revision=revision,
        changes={"action": operation, "status": "requested"},
    )
    service.session.add(audit)
    await service.session.commit()
    try:
        receipt = await receiver_request(
            auth_runtime(request),
            target,
            CacheReceiverTarget(node_id=node_id, source_epoch=str(epoch)),
            response_type,
            operation=operation,
        )
    except ReceiverFailure as exc:
        audit.changes = {"action": operation, "status": "unconfirmed", "receiver": exc.state}
        await service.session.commit()
        if exc.state == "unsupported":
            raise AuthFailure(409, 40925, "此节点接收器尚未支持缓存清理") from exc
        raise AuthFailure(502, 50225, "未能确认清理结果，请重新读取节点状态") from exc
    changes = {
        "action": operation,
        "status": "completed",
        "removed_files": receipt.removed_files,
        "removed_bytes": receipt.removed_bytes,
    }
    if operation == "clear":
        # The disk side effect is verified at this point. Requeue current revisions
        # after rechecking the binding; DB locks must never span the receiver call.
        await lock_references(service.session)
        await service.session.refresh(site, with_for_update=True)
        current_target = (await read_directory(service.session, service.config)).targets.get(
            node_id
        )
        same_receiver = bool(
            site.source_epoch == epoch
            and target
            and current_target
            and current_target.enabled
            and current_target.settings_receiver_url == target.settings_receiver_url
            and current_target.fastapi_to_nextjs_token_env == target.fastapi_to_nextjs_token_env
            and current_target.encrypted_outbound == target.encrypted_outbound
        )
        if same_receiver:
            await ContentPublication(service.session, service.config).stage_retry(
                actor, node_id=node_id
            )
            await service.stage_retry(node_id, actor)
        changes["sync_queued"] = same_receiver
    audit.changes = changes
    await service.session.commit()
    return JSONResponseSuccess(data=receipt)


@router.post("/{node_id}/cache/prune", response_model=PublicResponse[CachePruneReceipt])
async def prune_cache(node_id: NodeId, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return await delete_cache(node_id, request, ctx, "prune", CachePruneReceipt)


@router.post("/{node_id}/cache/clear", response_model=PublicResponse[CacheClearReceipt])
async def clear_cache(node_id: NodeId, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return await delete_cache(node_id, request, ctx, "clear", CacheClearReceipt)
