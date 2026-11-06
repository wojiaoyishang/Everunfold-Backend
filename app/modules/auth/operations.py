"""Caller-owned account mutation receipts; secret inputs get salted Argon2 fingerprints."""

import hashlib
import json
from typing import Any
from uuid import UUID

from anyio import CapacityLimiter, to_thread
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AccountOperation
from app.modules.auth.security import hash_password, verify_password
from app.modules.auth.service import AuthFailure


class AccountOperations:
    def __init__(self, session: AsyncSession, limiter: CapacityLimiter) -> None:
        self.session = session
        self.limiter = limiter

    @staticmethod
    def canonical(payload: BaseModel | dict[str, Any]) -> str:
        value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    async def read(
        self, operation_id: UUID, actor_id: UUID, kind: str, payload: BaseModel | dict[str, Any]
    ) -> dict[str, Any] | None:
        row = await self.session.scalar(
            select(AccountOperation).where(AccountOperation.id == operation_id).with_for_update()
        )
        if row is None:
            return None
        value = self.canonical(payload)
        matching = (
            await to_thread.run_sync(verify_password, row.fingerprint, value, limiter=self.limiter)
            if row.fingerprint.startswith("$argon2")
            else row.fingerprint == hashlib.sha256(value.encode()).hexdigest()
        )
        if row.actor_id != actor_id or row.kind != kind or not matching:
            raise AuthFailure(409, 40960, "此操作标识已用于其他请求，请重新发起操作")
        return row.response

    async def remember(
        self,
        operation_id: UUID,
        actor_id: UUID,
        kind: str,
        payload: BaseModel | dict[str, Any],
        response: dict[str, Any],
        *,
        sensitive: bool = False,
    ) -> None:
        value = self.canonical(payload)
        fingerprint = (
            await to_thread.run_sync(hash_password, value, limiter=self.limiter)
            if sensitive
            else hashlib.sha256(value.encode()).hexdigest()
        )
        self.session.add(
            AccountOperation(
                id=operation_id,
                actor_id=actor_id,
                kind=kind,
                fingerprint=fingerprint,
                response=response,
            )
        )
