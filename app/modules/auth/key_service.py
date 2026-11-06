"""Opaque user keys: explicit ceilings, live owner state and one-time secret delivery."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AuthConfig
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.callback_events import enqueue_callback
from app.modules.auth.credential_policy import (
    application_scopes,
    effective_permissions,
    owner_permissions,
)
from app.modules.auth.credentials_models import ApiApplication, ApiKey
from app.modules.auth.credentials_schemas import (
    IntrospectionInput,
    IntrospectionState,
    KeyApplication,
    KeyCreate,
    KeyList,
    KeyMutation,
    KeyOptions,
    KeyQuery,
    KeyRevoke,
    KeyState,
    KeySubject,
)
from app.modules.auth.models import User
from app.modules.auth.permissions import Actor, ResourceScope
from app.modules.auth.service import AuthFailure

TOKEN_PATTERN = re.compile(r"^efk_([a-f0-9]{32})_[A-Za-z0-9_-]{43}$")


def key_digest(token: str) -> str:
    return hashlib.sha256(b"everunfold.api-key.v1:" + token.encode("ascii")).hexdigest()


@dataclass
class KeyIdentity:
    key: ApiKey
    user: User
    app: ApiApplication
    permissions: dict[str, ResourceScope]

    def actor(self) -> Actor:
        return Actor(
            self.user.id,
            self.user.username,
            self.user.display_name,
            self.user.role,
            self.permissions,
            "api_key",
            self.key.id,
            self.key.token_hash,
        )


async def identity_by_digest(
    session: AsyncSession, digest: str, *, lock: bool = False
) -> KeyIdentity | None:
    statement = (
        select(ApiKey).where(ApiKey.token_hash == digest).execution_options(populate_existing=True)
    )
    key = await session.scalar(statement.with_for_update(read=True) if lock else statement)
    if key is None or key.revoked_at is not None or key.expires_at <= datetime.now(UTC):
        return None
    user_statement = (
        select(User).where(User.id == key.user_id).execution_options(populate_existing=True)
    )
    user = await session.scalar(
        user_statement.with_for_update(read=True) if lock else user_statement
    )
    app_statement = (
        select(ApiApplication)
        .where(ApiApplication.id == key.app_id)
        .execution_options(populate_existing=True)
    )
    app = await session.scalar(app_statement.with_for_update(read=True) if lock else app_statement)
    if user is None or app is None:
        return None
    permissions = effective_permissions(user, app, key)
    if not permissions:
        return None
    return KeyIdentity(key, user, app, permissions)


async def identity_by_token(session: AsyncSession, token: str) -> KeyIdentity | None:
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    digest = key_digest(token)
    identity = await identity_by_digest(session, digest)
    if identity is None or not hmac.compare_digest(identity.key.token_hash, digest):
        return None
    return identity


def key_state(user: User, app: ApiApplication, key: ApiKey) -> KeyState:
    permissions = effective_permissions(user, app, key)
    status = (
        "revoked"
        if key.revoked_at is not None or key.user_key_epoch != user.key_epoch
        else "expired"
        if key.expires_at <= datetime.now(UTC)
        else "active"
        if permissions
        else "unavailable"
    )
    return KeyState(
        id=key.id,
        app_id=app.id,
        application_name=app.name,
        audience=key.audience,
        name=key.name,
        masked="efk_" + key.id.hex[:8] + "…",
        permissions=cast(dict[str, ResourceScope], key.permissions),
        effective_permissions=permissions,
        status=cast(Literal["active", "revoked", "expired", "unavailable"], status),
        revision=key.revision,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


class KeyService:
    def __init__(self, accounts: AccountsService) -> None:
        self.accounts = accounts
        self.session = accounts.session
        self.operations = accounts.operations

    async def options(self, actor: User) -> KeyOptions:
        applications = await self.session.scalars(
            select(ApiApplication)
            .where(ApiApplication.active.is_(True))
            .order_by(ApiApplication.id)
        )
        entries = []
        for app in applications:
            available = owner_permissions(actor, app)
            if available:
                entries.append(
                    KeyApplication(
                        id=app.id,
                        name=app.name,
                        audience=app.audience,
                        permissions=available,
                        scopes=[
                            scope for scope in application_scopes(app) if scope.code in available
                        ],
                    )
                )
        return KeyOptions(applications=entries)

    async def list_keys(self, actor: User, query: KeyQuery | None = None) -> KeyList:
        query = query or KeyQuery()
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(ApiKey).where(ApiKey.user_id == actor.id)
            )
            or 0
        )
        records = await self.session.execute(
            select(ApiKey, ApiApplication)
            .join(ApiApplication, ApiKey.app_id == ApiApplication.id)
            .where(ApiKey.user_id == actor.id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        items = [key_state(actor, app, key) for key, app in records]
        return KeyList(items=items, total=total, page=query.page, page_size=query.page_size)

    async def create(self, actor: User, payload: KeyCreate) -> KeyMutation:
        if actor.must_change_password:
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        cached = await self.operations.read(payload.operation_id, actor.id, "key_create", payload)
        if cached is not None:
            return KeyMutation.model_validate(cached)
        await self.accounts.confirm_password(actor, payload.current_password)
        app = await self.session.get(ApiApplication, payload.app_id)
        if app is None or not app.active:
            raise AuthFailure(422, 42262, "接入应用未启用")
        available = owner_permissions(actor, app)
        scopes = {scope.code: scope for scope in application_scopes(app)}
        if not payload.permissions or len(payload.permissions) > 64:
            raise AuthFailure(422, 42262, "请至少选择一项可用权限")
        for code, scope in payload.permissions.items():
            if code not in available or (scope == "all" and available[code] != "all"):
                raise AuthFailure(403, 40360, "不能授予超出自身权限的授权")
            if scope == "own" and not scopes[code].owner_scope:
                raise AuthFailure(422, 42260, "权限或资源范围不可用")
        now = datetime.now(UTC)
        if payload.expires_at <= now or payload.expires_at > now + timedelta(days=3650):
            raise AuthFailure(422, 42262, "请选择未来十年内的到期时间")
        key_id = uuid4()
        token = f"efk_{key_id.hex}_{secrets.token_urlsafe(32)}"
        key = ApiKey(
            id=key_id,
            user_id=actor.id,
            app_id=app.id,
            audience=app.audience,
            name=payload.name.strip(),
            token_hash=key_digest(token),
            permissions=dict(payload.permissions),
            user_key_epoch=actor.key_epoch,
            created_at=now,
            expires_at=payload.expires_at,
            revision=1,
        )
        if not key.name:
            raise AuthFailure(422, 42262, "密钥名称不能为空")
        self.session.add(key)
        await self.session.flush()
        self.accounts.audit(actor, "api_key_created", actor, key_id=str(key.id), app_id=app.id)
        result = KeyMutation(key=key_state(actor, app, key))
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "key_create",
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result.model_copy(update={"secret": token})

    async def revoke(
        self, actor: User, payload: KeyRevoke, *, owner: User | None = None
    ) -> KeyList:
        owner = owner or actor
        envelope = {"owner_id": str(owner.id), **payload.model_dump(mode="json")}
        cached = await self.operations.read(payload.operation_id, actor.id, "key_revoke", envelope)
        if cached is None:
            key = await self.session.get(ApiKey, payload.key_id, with_for_update=True)
            if key is None or key.user_id != owner.id:
                raise AuthFailure(404, 40461, "密钥不存在")
            if key.revoked_at is None:
                key.revoked_at = datetime.now(UTC)
                key.revision += 1
                app = await self.session.get(ApiApplication, key.app_id)
                user = await self.session.get(User, key.user_id)
                if app and user:
                    enqueue_callback(self.session, app, "key.revoked", user=user, key=key)
                self.accounts.audit(
                    actor, "api_key_revoked", user or actor, key_id=str(key.id), app_id=key.app_id
                )
            await self.operations.remember(
                payload.operation_id, actor.id, "key_revoke", envelope, {"done": True}
            )
            await self.session.commit()
        return await self.list_keys(owner)


async def introspect_key(
    session: AsyncSession, app: ApiApplication, config: AuthConfig, payload: IntrospectionInput
) -> IntrospectionState:
    identity = await identity_by_token(session, payload.token)
    if identity is None or identity.app.id != app.id:
        return IntrospectionState(active=False)
    now = datetime.now(UTC)
    identity.key.last_used_at = now
    result = IntrospectionState(
        active=True,
        app_id=app.id,
        audience=identity.key.audience,
        key_id=identity.key.id,
        subject=KeySubject(id=identity.user.id, display_name=identity.user.display_name),
        permissions=identity.permissions,
        app_version=identity.app.auth_version,
        user_version=identity.user.auth_version,
        key_version=identity.key.revision,
        checked_at=now,
        expires_at=identity.key.expires_at,
        cache_until=min(
            identity.key.expires_at,
            now + timedelta(seconds=config.key_cache_seconds if payload.mode == "read" else 0),
        ),
    )
    await session.commit()
    return result
