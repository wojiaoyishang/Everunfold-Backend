"""Application registry, direction-specific credentials and explicit delivery commands."""

from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx2 as httpx
from sqlalchemy import select

from app.core.config import Settings
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.application_signatures import application_secret, key_id, signing_available
from app.modules.auth.callback_events import enqueue_callback
from app.modules.auth.credential_policy import application_scopes
from app.modules.auth.credentials_models import ApiApplication, AuthCallbackEvent
from app.modules.auth.credentials_schemas import (
    ApplicationCreate,
    ApplicationFields,
    ApplicationList,
    ApplicationMutation,
    ApplicationRotate,
    ApplicationSave,
    ApplicationState,
    CallbackCommand,
    CallbackState,
)
from app.modules.auth.models import User
from app.modules.auth.permissions import BUILTIN_APP, CAPABILITIES
from app.modules.auth.service import AuthFailure


def callback_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as error:
        raise AuthFailure(422, 42263, "回调地址无效") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise AuthFailure(422, 42263, "回调地址无效")
    hostname = "[" + host + "]" if ":" in host else host
    suffix = (
        ""
        if port is None or (parsed.scheme, port) in {("http", 80), ("https", 443)}
        else ":" + str(port)
    )
    return parsed.scheme + "://" + hostname + suffix


def callback_url(value: str) -> str:
    if not value:
        return ""
    callback_origin(value)
    return str(httpx.URL(value))


class ApplicationService:
    def __init__(self, accounts: AccountsService, config: Settings) -> None:
        self.accounts, self.config = accounts, config
        self.session, self.operations = accounts.session, accounts.operations

    def validate_fields(self, fields: ApplicationFields, audience: str) -> None:
        if not fields.name.strip():
            raise AuthFailure(422, 42263, "应用名称不能为空")
        if fields.callback_url:
            origin = callback_origin(fields.callback_url)
            allowed = {
                callback_origin(value) for value in self.config.auth.callback_allowed_origins
            }
            if origin not in allowed:
                raise AuthFailure(422, 42263, "回调地址不在部署允许的来源中")
        seen = set()
        for scope in fields.scopes:
            if scope.code in seen or not scope.label.strip():
                raise AuthFailure(422, 42263, "权限代码重复或名称为空")
            seen.add(scope.code)
            if audience == BUILTIN_APP:
                definition = CAPABILITIES.get(scope.code)
                if not definition or not definition[1] or (scope.owner_scope and not definition[2]):
                    raise AuthFailure(422, 42263, "此权限不能用于博客API Key")

    async def find(self, app_id: str, *, lock: bool = False) -> ApiApplication:
        row = await self.session.get(ApiApplication, app_id, with_for_update=lock)
        if row is None:
            raise AuthFailure(404, 40462, "接入应用不存在")
        return row

    async def state(self, app: ApiApplication) -> ApplicationState:
        events = await self.session.scalars(
            select(AuthCallbackEvent)
            .where(AuthCallbackEvent.app_id == app.id)
            .order_by(AuthCallbackEvent.created_at.desc(), AuthCallbackEvent.id)
            .limit(20)
        )
        return ApplicationState(
            id=app.id,
            name=app.name,
            audience=app.audience,
            active=app.active,
            callback_url=app.callback_url,
            scopes=application_scopes(app),
            revision=app.revision,
            auth_version=app.auth_version,
            query_key_id=key_id(app, "query"),
            callback_key_id=key_id(app, "callback"),
            query_url=self.config.BACKEND_BASE_URL.rstrip("/") + "/v1/auth/api-keys/introspect",
            signing_available=signing_available(self.config.auth),
            builtin=app.id == BUILTIN_APP,
            callbacks=[
                CallbackState(
                    id=event.id,
                    event_type=event.event_type,
                    status=cast(Literal["pending", "delivered", "failed", "skipped"], event.status),
                    attempts=event.attempts,
                    created_at=event.created_at,
                    received_at=event.received_at,
                    applied_at=event.applied_at,
                    last_error=event.last_error,
                )
                for event in events
            ],
        )

    async def list(self) -> ApplicationList:
        apps = list(await self.session.scalars(select(ApiApplication).order_by(ApiApplication.id)))
        return ApplicationList(items=[await self.state(app) for app in apps])

    async def create(self, actor: User, payload: ApplicationCreate) -> ApplicationMutation:
        cached = await self.operations.read(
            payload.operation_id, actor.id, "application_create", payload
        )
        if cached is not None:
            return ApplicationMutation.model_validate(cached)
        await self.accounts.confirm_password(actor, payload.current_password)
        if await self.session.get(ApiApplication, payload.app_id) is not None:
            raise AuthFailure(409, 40964, "应用标识已存在")
        audience = payload.app_id if payload.audience == "self" else BUILTIN_APP
        self.validate_fields(payload.value, audience)
        query_secret = application_secret(self.config.auth, payload.app_id, 1, "query")
        callback_secret = application_secret(self.config.auth, payload.app_id, 1, "callback")
        now = datetime.now(UTC)
        app = ApiApplication(
            id=payload.app_id,
            name=payload.value.name.strip(),
            audience=audience,
            active=payload.value.active,
            scopes=[scope.model_dump() for scope in payload.value.scopes],
            callback_url=callback_url(payload.value.callback_url),
            revision=1,
            auth_version=1,
            query_key_version=1,
            callback_key_version=1,
            created_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(app)
        await self.session.flush()
        self.accounts.audit(actor, "application_created", actor, app_id=app.id)
        result = ApplicationMutation(application=await self.state(app))
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "application_create",
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result.model_copy(
            update={"query_secret": query_secret, "callback_secret": callback_secret}
        )

    async def save(self, actor: User, app_id: str, payload: ApplicationSave) -> ApplicationMutation:
        envelope = {"app_id": app_id, **payload.model_dump(mode="json")}
        cached = await self.operations.read(
            payload.operation_id, actor.id, "application_save", envelope
        )
        if cached is not None:
            return ApplicationMutation.model_validate(cached)
        app = await self.find(app_id, lock=True)
        if app.revision != payload.expected_revision:
            raise AuthFailure(409, 40964, "应用设置已被修改，请重新读取后合并更改")
        await self.accounts.confirm_password(actor, payload.current_password)
        self.validate_fields(payload.value, app.audience)
        if app.id == BUILTIN_APP and payload.value.scopes:
            raise AuthFailure(422, 42263, "内置应用的权限目录由代码维护")
        if payload.value.callback_url and app.callback_key_version < 1:
            raise AuthFailure(422, 42263, "请先生成应用签名凭据")
        app.name, app.active = payload.value.name.strip(), payload.value.active
        app.callback_url = callback_url(payload.value.callback_url)
        if app.id != BUILTIN_APP:
            app.scopes = [scope.model_dump() for scope in payload.value.scopes]
        app.revision += 1
        app.updated_at = datetime.now(UTC)
        enqueue_callback(self.session, app, "application.authorization_changed")
        self.accounts.audit(actor, "application_updated", actor, app_id=app.id, active=app.active)
        await self.session.flush()
        result = ApplicationMutation(application=await self.state(app))
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "application_save",
            envelope,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def rotate(
        self, actor: User, app_id: str, payload: ApplicationRotate
    ) -> ApplicationMutation:
        envelope = {"app_id": app_id, **payload.model_dump(mode="json")}
        cached = await self.operations.read(
            payload.operation_id, actor.id, "application_rotate", envelope
        )
        if cached is not None:
            return ApplicationMutation.model_validate(cached)
        app = await self.find(app_id, lock=True)
        if app.revision != payload.expected_revision:
            raise AuthFailure(409, 40964, "应用设置已被修改，请重新读取后合并更改")
        await self.accounts.confirm_password(actor, payload.current_password)
        query_secret = callback_secret = None
        if payload.direction in {"query", "both"}:
            version = app.query_key_version + 1
            query_secret = application_secret(self.config.auth, app.id, version, "query")
            app.query_key_version = version
        if payload.direction in {"callback", "both"}:
            version = app.callback_key_version + 1
            callback_secret = application_secret(self.config.auth, app.id, version, "callback")
            app.callback_key_version = version
        app.revision += 1
        app.updated_at = datetime.now(UTC)
        enqueue_callback(self.session, app, "application.credentials_rotated")
        self.accounts.audit(
            actor,
            "application_credentials_rotated",
            actor,
            app_id=app.id,
            direction=payload.direction,
        )
        await self.session.flush()
        result = ApplicationMutation(application=await self.state(app))
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "application_rotate",
            envelope,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result.model_copy(
            update={"query_secret": query_secret, "callback_secret": callback_secret}
        )

    async def command(self, actor: User, app_id: str, payload: CallbackCommand) -> ApplicationState:
        envelope = {"app_id": app_id, **payload.model_dump(mode="json")}
        cached = await self.operations.read(
            payload.operation_id, actor.id, "application_callback", envelope
        )
        app = await self.find(app_id, lock=True)
        if cached is None:
            if not app.callback_url or app.callback_key_version < 1:
                raise AuthFailure(422, 42263, "请先配置回调地址与签名凭据")
            self.validate_fields(
                ApplicationFields(name=app.name, callback_url=app.callback_url), app.audience
            )
            if payload.action == "retry":
                events = await self.session.scalars(
                    select(AuthCallbackEvent)
                    .where(AuthCallbackEvent.app_id == app.id, AuthCallbackEvent.status == "failed")
                    .with_for_update()
                )
                for event in events:
                    event.status, event.attempts = "pending", 0
                    event.available_at = datetime.now(UTC)
                    event.lease_id = event.lease_until = None
            else:
                enqueue_callback(
                    self.session,
                    app,
                    "application.test" if payload.action == "test" else "application.resync",
                    increment=payload.action != "test",
                )
            self.accounts.audit(
                actor, "application_callback_command", actor, app_id=app.id, command=payload.action
            )
            await self.operations.remember(
                payload.operation_id, actor.id, "application_callback", envelope, {"done": True}
            )
            await self.session.commit()
        return await self.state(app)
