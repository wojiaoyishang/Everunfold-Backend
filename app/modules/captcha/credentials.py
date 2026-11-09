"""Machine admission uses explicit grants, live authorization and a per-key budget."""

import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.credentials_models import ApiApplication, ApiKey
from app.modules.auth.key_service import KeyIdentity, identity_by_digest, identity_by_token
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.permissions import BUILTIN_APP
from app.modules.auth.service import AuthFailure


@dataclass(frozen=True)
class ReaderCredential:
    digest: str = field(repr=False)
    key_id: UUID
    user_id: UUID
    exempt: bool


def unauthorized() -> AuthFailure:
    return AuthFailure(401, 40161, "API Key无效或已撤销")


async def authenticate(session: AsyncSession, authorization: str) -> KeyIdentity:
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise unauthorized()
    identity = await identity_by_token(session, parts[1])
    if identity is None or identity.key.audience != BUILTIN_APP:
        raise unauthorized()
    return identity


async def fence(session: AsyncSession, credential: ReaderCredential, action: str) -> None:
    # Lock the owner before the key, matching account authorization changes. Take an
    # exclusive key lock immediately: updating last_used_at must not upgrade two
    # simultaneous SHARE locks. All locks precede the site's publication fence.
    identity = await identity_by_digest(session, credential.digest)
    if identity is None or identity.key.audience != BUILTIN_APP:
        raise unauthorized()
    await session.scalar(select(User).where(User.id == identity.user.id).with_for_update(read=True))
    await session.scalar(
        select(ApiApplication)
        .where(ApiApplication.id == identity.app.id)
        .with_for_update(read=True)
    )
    await session.scalar(select(ApiKey).where(ApiKey.id == identity.key.id).with_for_update())
    identity = await identity_by_digest(session, credential.digest)
    if (
        identity is None
        or identity.key.audience != BUILTIN_APP
        or identity.key.id != credential.key_id
        or not hmac.compare_digest(identity.key.token_hash, credential.digest)
    ):
        raise unauthorized()
    if credential.exempt and f"captcha:{action}_bypass" not in identity.permissions:
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    identity.key.last_used_at = datetime.now(UTC)
    if credential.exempt:
        session.add(
            AuthAuditEvent(
                user_id=identity.user.id,
                target_user_id=identity.user.id,
                action="api_key_captcha_bypass",
                created_at=datetime.now(UTC),
                details={"key_id": str(identity.key.id), "action": action},
            )
        )
