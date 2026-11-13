"""Private profiles and account administration, with one caller-owned mutation transaction."""

import hashlib
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.accounts_schemas import (
    AccountProfile,
    AccountUserList,
    PasswordChange,
    PasswordReset,
    ProfileFields,
    ProfileSave,
    SessionItem,
    SessionList,
    SessionRevoke,
    UserCreate,
    UserSave,
)
from app.modules.auth.callback_events import invalidate_user_keys
from app.modules.auth.credential_policy import application_scopes
from app.modules.auth.credentials_models import ApiApplication
from app.modules.auth.models import AuthAuditEvent, AuthRateLimit, BrowserSession, User
from app.modules.auth.operations import AccountOperations
from app.modules.auth.permissions import BUILTIN_APP, CAPABILITIES, ResourceScope, user_permissions
from app.modules.auth.preset_schemas import UserPresetStamp
from app.modules.auth.security import (
    hash_password,
    normalize_username,
    token_digest,
    verify_password,
)
from app.modules.auth.service import AuthFailure, AuthService, SessionResult
from app.modules.notifications.catalog import USER_CREATED
from app.modules.notifications.events import publish_account_created
from app.modules.settings.languages import require_language


async def account_mutation_lock(session: AsyncSession) -> None:
    # Serialize the small account management plane, including last-admin checks
    # and operation IDs. Acquire before authentication, never after session locks.
    await session.execute(text("SELECT pg_advisory_xact_lock(7211293369)"))


def profile(user: User) -> AccountProfile:
    return AccountProfile(
        id=user.id,
        username=user.username,
        role=cast(Literal["admin", "user"], user.role),
        active=user.active,
        revision=user.revision,
        auth_version=user.auth_version,
        must_change_password=user.must_change_password,
        grants=cast(dict[str, dict[str, ResourceScope]], user.grants),
        permission_preset=(
            UserPresetStamp.model_validate(user.permission_preset)
            if user.permission_preset
            else None
        ),
        display_name=user.display_name,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        email_verification_source=(
            "mailbox" if user.email_verified_at else "admin" if user.email_reviewed_at else None
        ),
        email_verification_at=user.email_verified_at or user.email_reviewed_at,
        avatar_url=user.avatar_url,
        avatar_id=user.avatar_id,
        bio=user.bio,
        website=user.website,
        preferred_locale=user.preferred_locale,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


class AccountsService:
    def __init__(self, session: AsyncSession, auth: AuthService) -> None:
        self.session = session
        self.auth = auth
        self.operations = AccountOperations(session, auth.password_limiter)

    def audit(self, actor: User, action: str, target: User, **details: object) -> None:
        self.session.add(
            AuthAuditEvent(
                user_id=actor.id,
                target_user_id=target.id,
                action=action,
                details=details,
                created_at=datetime.now(UTC),
            )
        )

    async def target(self, user_id: UUID, *, lock: bool = False) -> User:
        statement = select(User).where(User.id == user_id).execution_options(populate_existing=True)
        user = await self.session.scalar(statement.with_for_update() if lock else statement)
        if user is None:
            raise AuthFailure(404, 40460, "账号不存在")
        return user

    @staticmethod
    def version(user: User, expected: int) -> None:
        if user.revision != expected:
            raise AuthFailure(409, 40961, "账号资料已被修改，请重新读取后合并更改")

    async def confirm_password(self, actor: User, password: str) -> None:
        key = "confirm-password:" + str(actor.id)
        allowed = await self.auth.repo.consume_limit(
            key,
            self.auth.config.login_attempts_per_account,
            self.auth.config.login_window_seconds,
            datetime.now(UTC),
        )
        if not allowed:
            await self.session.commit()
            raise AuthFailure(429, 42901, "尝试次数较多，请稍后再试")
        valid = await to_thread.run_sync(
            verify_password, actor.password_hash, password, limiter=self.auth.password_limiter
        )
        if not valid:
            self.audit(actor, "password_confirmation_failed", actor)
            await self.session.commit()
            raise AuthFailure(403, 40361, "当前密码不正确")
        await self.session.execute(
            delete(AuthRateLimit).where(
                AuthRateLimit.key_hash == hashlib.sha256(key.encode()).hexdigest()
            )
        )

    async def validate_grants(
        self, actor: User, grants: dict[str, dict[str, ResourceScope]], role: str
    ) -> None:
        if role == "admin" and actor.role != "admin":
            raise AuthFailure(403, 40360, "不能授予超出自身权限的授权")
        if len(grants) > 64 or sum(len(items) for items in grants.values()) > 256:
            raise AuthFailure(422, 42260, "授权项目过多")
        for app_id, items in grants.items():
            if app_id == BUILTIN_APP:
                definitions = {
                    name: (label, key, owner) for name, (label, key, owner) in CAPABILITIES.items()
                }
            else:
                app = await self.session.get(ApiApplication, app_id)
                if app is None or app.audience != app_id:
                    raise AuthFailure(422, 42260, "接入应用不存在")
                definitions = {
                    item.code: (item.label, item.api_key, item.owner_scope)
                    for item in application_scopes(app)
                }
            available = (
                {name: "all" for name in definitions}
                if actor.role == "admin"
                else user_permissions(actor, app_id)
            )
            for name, scope in items.items():
                definition = definitions.get(name)
                if definition is None or (scope == "own" and not definition[2]):
                    raise AuthFailure(422, 42260, "权限或资源范围不可用")
                if name not in available or (scope == "all" and available[name] != "all"):
                    raise AuthFailure(403, 40360, "不能授予超出自身权限的授权")

    async def assign_profile(self, user: User, value: ProfileFields) -> None:
        await require_language(self.session, value.preferred_locale)
        from app.modules.auth.avatar_models import UserAvatar
        from app.modules.media.locking import lock_references
        from app.modules.media.models import MediaAsset

        if "avatar_id" in value.model_fields_set:
            await lock_references(self.session)
            if value.avatar_id is None:
                # Preserve an unchanged legacy URL while editing other profile fields.
                if user.avatar_id is not None or value.avatar_url != user.avatar_url:
                    user.avatar_id, user.avatar_url = None, ""
            else:
                owned = await self.session.get(UserAvatar, value.avatar_id)
                asset = (
                    await self.session.get(MediaAsset, owned.asset_id)
                    if owned and owned.asset_id
                    else None
                )
                if (
                    not owned
                    or owned.user_id != user.id
                    or owned.removed_at is not None
                    or not asset
                    or asset.status != "ready"
                    or asset.deleted_at is not None
                    or asset.purge_pending
                ):
                    raise AuthFailure(422, 42265, "请选择本人头像图库中可用的图片")
                user.avatar_id, user.avatar_url = owned.id, "/media/" + asset.object_key
        elif value.avatar_url != user.avatar_url:
            if value.avatar_url:
                raise AuthFailure(422, 42265, "请通过个人头像图库设置头像")
            user.avatar_id, user.avatar_url = None, ""
        email_changed = value.email != user.email
        if email_changed and value.email:
            existing = await self.session.scalar(
                select(User.id).where(
                    User.id != user.id,
                    func.lower(func.btrim(User.email)) == value.email.strip().lower(),
                )
            )
            if existing is not None:
                raise AuthFailure(409, 40975, "邮箱无法绑定，请使用其他邮箱")
        for key, item in value.model_dump().items():
            if key not in {"avatar_id", "avatar_url"}:
                setattr(user, key, item)
        if email_changed:
            user.email_verified_at = None
            user.email_reviewed_at = None
        user.revision += 1
        user.updated_at = datetime.now(UTC)

    async def read_profile(self, actor: User) -> AccountProfile:
        return profile(actor)

    async def save_profile(self, actor: User, payload: ProfileSave) -> AccountProfile:
        cached = await self.operations.read(payload.operation_id, actor.id, "profile", payload)
        if cached is not None:
            return AccountProfile.model_validate(cached)
        user = await self.target(actor.id, lock=True)
        self.version(user, payload.expected_revision)
        await self.assign_profile(user, payload.value)
        self.audit(actor, "profile_updated", user)
        result = profile(user)
        await self.operations.remember(
            payload.operation_id, actor.id, "profile", payload, result.model_dump(mode="json")
        )
        await self.session.commit()
        return result

    async def list_users(
        self, q: str, page: int, page_size: int, active: bool | None = None
    ) -> AccountUserList:
        statement = select(User)
        if q.strip():
            query = (
                "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            statement = statement.where(
                or_(
                    User.username.ilike(query, escape="\\"),
                    User.display_name.ilike(query, escape="\\"),
                    User.email.ilike(query, escape="\\"),
                )
            )
        if active is not None:
            statement = statement.where(User.active == active)
        total = int(
            await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = await self.session.scalars(
            statement.order_by(User.created_at.desc(), User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return AccountUserList(
            items=[profile(row) for row in rows], total=total, page=page, page_size=page_size
        )

    async def create_user(self, actor: User, payload: UserCreate) -> AccountProfile:
        envelope = payload.model_dump(mode="json")
        if "preset" not in payload.model_fields_set:
            envelope.pop("preset", None)
        cached = await self.operations.read(payload.operation_id, actor.id, "user_create", envelope)
        if cached is not None:
            return AccountProfile.model_validate(cached)
        grants, stamp = payload.grants, None
        if payload.preset is not None:
            from app.modules.auth.preset_service import resolve_preset

            grants, stamp = await resolve_preset(self.session, payload.preset, payload.role)
        await self.validate_grants(actor, grants, payload.role)
        name = normalize_username(payload.username)
        if await self.auth.repo.user_by_name(name) is not None:
            raise AuthFailure(409, 40962, "用户名已存在")
        display_name = payload.display_name.strip()
        if not display_name:
            raise AuthFailure(422, 42260, "显示名称不能为空")
        now = datetime.now(UTC)
        user = User(
            id=uuid4(),
            username=name,
            display_name=display_name,
            password_hash=await to_thread.run_sync(
                hash_password, payload.password, limiter=self.auth.password_limiter
            ),
            role=payload.role,
            active=True,
            grants=grants,
            permission_preset=stamp,
            must_change_password=True,
            created_at=now,
            updated_at=now,
        )
        self.session.add(user)
        await self.session.flush()
        self.audit(actor, "user_created", user, role=user.role)
        await publish_account_created(self.session, USER_CREATED, user, actor.id)
        result = profile(user)
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "user_create",
            envelope,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def save_user(self, actor: User, user_id: UUID, payload: UserSave) -> AccountProfile:
        envelope = {"user_id": str(user_id), **payload.model_dump(mode="json")}
        if "preset" not in payload.model_fields_set:
            envelope.pop("preset", None)
        cached = await self.operations.read(payload.operation_id, actor.id, "user_save", envelope)
        if cached is not None:
            return AccountProfile.model_validate(cached)
        user = await self.target(user_id, lock=True)
        self.version(user, payload.expected_revision)
        if user.role == "admin" and actor.role != "admin":
            raise AuthFailure(403, 40360, "不能修改管理员账号")
        grants, stamp = payload.grants, user.permission_preset
        if payload.preset is not None:
            from app.modules.auth.preset_service import resolve_preset

            grants, stamp = await resolve_preset(self.session, payload.preset, payload.role)
        elif (
            "preset" in payload.model_fields_set
            or user.role != payload.role
            or user.grants != grants
        ):
            stamp = None
        await self.validate_grants(actor, grants, payload.role)
        auth_changed = (user.role, user.active, user.grants) != (
            payload.role,
            payload.active,
            grants,
        )
        if user.active and user.role == "admin" and (not payload.active or payload.role != "admin"):
            remaining = await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.active.is_(True), User.role == "admin", User.id != user.id)
            )
            if not remaining:
                raise AuthFailure(409, 40963, "必须保留至少一个有效管理员")
        await self.assign_profile(user, payload.value)
        if auth_changed:
            user.auth_version += 1
            if not payload.active:
                user.key_epoch += 1
            await self.session.execute(
                delete(BrowserSession).where(BrowserSession.user_id == user.id)
            )
        user.role, user.active = payload.role, payload.active
        user.grants = {app: dict(items) for app, items in grants.items()}
        user.permission_preset = stamp
        if auth_changed:
            await invalidate_user_keys(self.session, user)
        self.audit(
            actor,
            "user_updated",
            user,
            authorization_changed=auth_changed,
            active=user.active,
            role=user.role,
        )
        result = profile(user)
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "user_save",
            envelope,
            result.model_dump(mode="json"),
            sensitive=False,
        )
        await self.session.commit()
        return result

    async def sessions(self, actor: User, current_token: str) -> SessionList:
        rows = await self.session.scalars(
            select(BrowserSession)
            .where(
                BrowserSession.user_id == actor.id, BrowserSession.expires_at > datetime.now(UTC)
            )
            .order_by(BrowserSession.created_at.desc())
        )
        current = token_digest(current_token)
        return SessionList(
            items=[
                SessionItem(
                    id=row.token_hash,
                    current=row.token_hash == current,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
                for row in rows
            ]
        )

    async def revoke_session(
        self, actor: User, current_token: str, payload: SessionRevoke
    ) -> SessionList:
        cached = await self.operations.read(
            payload.operation_id, actor.id, "session_revoke", payload
        )
        if cached is None:
            if payload.session_id == token_digest(current_token):
                raise AuthFailure(422, 42261, "当前会话请使用退出登录")
            statement = delete(BrowserSession).where(BrowserSession.user_id == actor.id)
            if payload.session_id == "others":
                statement = statement.where(
                    BrowserSession.token_hash != token_digest(current_token)
                )
            else:
                statement = statement.where(BrowserSession.token_hash == payload.session_id)
            await self.session.execute(statement)
            self.audit(actor, "sessions_revoked", actor, others=payload.session_id == "others")
            await self.operations.remember(
                payload.operation_id, actor.id, "session_revoke", payload, {"done": True}
            )
            await self.session.commit()
        return await self.sessions(actor, current_token)

    async def change_password(
        self, actor: User, current_token: str, payload: PasswordChange
    ) -> SessionResult:
        cached = await self.operations.read(
            payload.operation_id, actor.id, "password_change", payload
        )
        if cached is not None:
            return await self.auth.read(current_token, "account")
        user = await self.target(actor.id, lock=True)
        self.version(user, payload.expected_revision)
        await self.confirm_password(user, payload.current_password)
        user.password_hash = await to_thread.run_sync(
            hash_password, payload.new_password, limiter=self.auth.password_limiter
        )
        user.must_change_password = False
        user.auth_version += 1
        user.revision += 1
        if payload.revoke_api_keys:
            user.key_epoch += 1
        user.updated_at = datetime.now(UTC)
        await self.session.execute(delete(BrowserSession).where(BrowserSession.user_id == user.id))
        await invalidate_user_keys(self.session, user)
        record, token = self.auth._new(datetime.now(UTC), user)
        result = SessionResult(self.auth._state(record, token, user), token)
        self.audit(user, "password_changed", user, revoke_api_keys=payload.revoke_api_keys)
        await self.operations.remember(
            payload.operation_id,
            user.id,
            "password_change",
            payload,
            {"done": True},
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def reset_password(
        self, actor: User, user_id: UUID, payload: PasswordReset
    ) -> AccountProfile:
        if actor.id == user_id:
            raise AuthFailure(422, 42261, "请到账号中心修改自己的密码")
        envelope = {"user_id": str(user_id), **payload.model_dump(mode="json")}
        cached = await self.operations.read(
            payload.operation_id, actor.id, "password_reset", envelope
        )
        if cached is not None:
            return AccountProfile.model_validate(cached)
        user = await self.target(user_id, lock=True)
        self.version(user, payload.expected_revision)
        if user.role == "admin" and actor.role != "admin":
            raise AuthFailure(403, 40360, "不能修改管理员账号")
        await self.confirm_password(actor, payload.current_password)
        user.password_hash = await to_thread.run_sync(
            hash_password, payload.new_password, limiter=self.auth.password_limiter
        )
        user.must_change_password = True
        user.auth_version += 1
        user.key_epoch += 1
        user.revision += 1
        user.updated_at = datetime.now(UTC)
        await self.session.execute(delete(BrowserSession).where(BrowserSession.user_id == user.id))
        self.audit(actor, "password_reset", user)
        await invalidate_user_keys(self.session, user)
        result = profile(user)
        await self.operations.remember(
            payload.operation_id,
            actor.id,
            "password_reset",
            envelope,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result
