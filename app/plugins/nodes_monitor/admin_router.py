"""Site-admin access to monitor data and proofs for frontend-local operations."""

import hashlib
import hmac
import time

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import Service, auth_runtime, cookie
from app.modules.auth.service import AuthFailure
from app.modules.settings.node_registry import read_targets
from app.modules.settings.nodes_schemas import MonitorStatus, NodeProof, NodeProofRequest
from app.modules.settings.service import SettingsService

from .status import read_status

router = APIRouter(
    prefix="/v1/admin/nodes-monitor",
    tags=["nodes-monitor"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 422, 503)},
)


@router.get("", response_model=PublicResponse[MonitorStatus])
async def monitor(request: Request, service: Service) -> JSONResponseSuccess:
    await service.require_admin(cookie(request), None, write=False)
    return JSONResponseSuccess(data=await read_status(auth_runtime(request)))


@router.post("/local-proof", response_model=PublicResponse[NodeProof])
async def local_proof(
    payload: NodeProofRequest, request: Request, service: Service
) -> JSONResponseSuccess:
    await service.require_admin(cookie(request), request.headers.get("X-CSRF-Token"), write=True)
    config = auth_runtime(request).settings
    target = (await read_targets(service.session, config)).get(payload.node_id)
    if target is None or not target.enabled:
        raise AuthFailure(404, 40420, "此节点尚未在后端启用")
    token = target.token(config, payload.node_id, "outbound")
    if len(token) < 32:
        raise AuthFailure(503, 50326, "此节点的运维授权尚未配置")
    source = (await SettingsService(service.session, config).site()).source_epoch
    expires = int(time.time()) + 30
    body = "\n".join(
        (
            "everunfold-node-console-v1",
            payload.node_id,
            str(source),
            payload.intent,
            payload.profile,
            payload.revision,
            payload.challenge,
            str(expires),
        )
    )
    signature = hmac.new(token.encode(), body.encode(), hashlib.sha256).hexdigest()
    return JSONResponseSuccess(data=NodeProof(expires=expires, signature=signature))
