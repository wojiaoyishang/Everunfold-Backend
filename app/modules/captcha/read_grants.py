"""In-memory reader grants also authorize reactions, bound to target, peer and policy."""

import json
from uuid import UUID

from fastapi import Request
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.router import auth_runtime, peer
from app.modules.auth.service import AuthFailure
from app.modules.captcha.comments import receipt_data, receipt_key
from app.modules.captcha.service import CaptchaContext, CaptchaService, guard


async def read_grant_target(request: Request, session: AsyncSession) -> UUID | None:
    receipt = request.headers.get("X-Everunfold-Read-Receipt", "")
    target = request.headers.get("X-Everunfold-Read-Target", "")
    if not receipt and not target:
        return None
    try:
        identity = UUID(target)
        if not receipt or len(receipt) > 100:
            raise ValueError("invalid receipt")
    except ValueError:
        raise AuthFailure(403, 40388, "请先解锁正文再操作评论") from None
    runtime = auth_runtime(request)
    permit = await CaptchaService(
        runtime, peer(request), request.headers.get("Authorization")
    ).policy("read", identity)
    await guard(session, permit)
    await verify_read_receipt(request, permit, receipt)
    return identity


async def verify_read_receipt(request: Request, permit: CaptchaContext, receipt: str) -> None:
    """Validate the shared proof after the caller fences the current read policy."""
    if not receipt or len(receipt) > 100:
        raise AuthFailure(403, 40388, "请先解锁正文再操作评论")
    runtime = auth_runtime(request)
    try:
        raw = await runtime.redis.get(receipt_key(request, receipt))
        valid = raw and json.loads(raw) == receipt_data(request, permit)
    except RedisError:
        raise AuthFailure(503, 50386, "评论服务暂不可用") from None
    if not valid:
        raise AuthFailure(403, 40388, "阅读验证已过期，请重新解锁正文")
