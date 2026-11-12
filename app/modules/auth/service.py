"""Request-scoped authentication use cases with durable commit-before-success semantics."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from anyio import CapacityLimiter, to_thread
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AuthConfig
from app.modules.auth.models import (
    AuthAuditEvent,
    BrowserSession,
    LoginChallenge,
    TwoFactorCredential,
    User,
)
from app.modules.auth.permissions import user_permissions
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import AuthSessionState, AuthUser
from app.modules.auth.security import (
    DUMMY_PASSWORD_HASH,
    csrf_token,
    new_session_token,
    normalize_username,
    token_digest,
    valid_csrf,
    valid_session_token,
    verify_password,
)


class AuthFailure(Exception):
    def __init__(
        self, status: int, code: int, message: str, retry_after: int | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class SessionResult:
    state: AuthSessionState
    cookie: str | None = None


class AuthService:
    def __init__(self, session: AsyncSession, config: AuthConfig, limiter: CapacityLimiter) -> None:
        self.session = session
        self.config = config
        self.repo = AuthRepository(session)
        self.password_limiter = limiter

    def _state(
        self, record: BrowserSession, token: str, user: User | None, *, mfa_required: bool = False
    ) -> AuthSessionState:
        return AuthSessionState(
            authenticated=user is not None,
            mfa_required=mfa_required,
            user=AuthUser(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=cast(Literal["admin", "user"], user.role),
                permissions=user_permissions(user),
                avatar_url=user.avatar_url,
                preferred_locale=user.preferred_locale,
                must_change_password=user.must_change_password,
            )
            if user is not None
            else None,
            csrf_token=csrf_token(token),
            expires_at=record.expires_at,
        )

    def _new(self, now: datetime, user: User | None = None) -> tuple[BrowserSession, str]:
        token = new_session_token()
        ttl = self.config.session_seconds if user else self.config.pre_session_seconds
        record = BrowserSession(
            token_hash=token_digest(token),
            user_id=user.id if user else None,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        self.session.add(record)
        return record, token

    async def _existing(
        self, token: str | None, now: datetime, *, lock: bool = True
    ) -> tuple[BrowserSession | None, User | None]:
        if not token:
            return None, None
        record = await self.repo.browser_session(token, now, lock=lock)
        user = await self.session.get(User, record.user_id) if record and record.user_id else None
        if record and record.user_id and (user is None or not user.active):
            await self.session.delete(record)
            return None, None
        return record, user

    async def require_admin(
        self, raw_token: str | None, supplied_csrf: str | None, *, write: bool
    ) -> User:
        """Authorize within the caller's transaction, without committing it."""
        token = valid_session_token(raw_token)
        record, user = await self._existing(token, datetime.now(UTC), lock=False)
        if record is None or user is None:
            raise AuthFailure(401, 40101, "请先登录")
        if user.role != "admin":
            raise AuthFailure(403, 40301, "当前账号没有后台访问权限")
        if user.must_change_password:
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        if write and (token is None or not valid_csrf(token, supplied_csrf)):
            raise AuthFailure(403, 40310, "登录状态已更新，请重新检查后重试")
        return user

    async def require_user(
        self, raw_token: str | None, supplied_csrf: str | None, *, write: bool
    ) -> User:
        token = valid_session_token(raw_token)
        record, user = await self._existing(token, datetime.now(UTC), lock=False)
        if record is None or user is None:
            raise AuthFailure(401, 40101, "请先登录")
        if write and (token is None or not valid_csrf(token, supplied_csrf)):
            raise AuthFailure(403, 40310, "登录状态已更新，请重新检查后重试")
        return user

    async def read(self, raw_token: str | None, peer: str, *, admin: bool = False) -> SessionResult:
        token = valid_session_token(raw_token)
        now = datetime.now(UTC)
        record, user = await self._existing(token, now)
        if admin and user is None:
            await self.session.commit()
            raise AuthFailure(401, 40101, "请先登录")
        if admin and user is not None and user.role != "admin" and not user_permissions(user):
            await self.session.commit()
            raise AuthFailure(403, 40301, "当前账号没有后台访问权限")
        if admin and user is not None and user.must_change_password:
            await self.session.commit()
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        cookie = None
        if record is None:
            allowed = await self.repo.consume_limit(
                "bootstrap:" + peer,
                self.config.bootstrap_attempts_per_ip,
                self.config.login_window_seconds,
                now,
            )
            if not allowed:
                await self.session.commit()
                raise AuthFailure(
                    429, 42901, "请求过于频繁，请稍后再试", self.config.login_window_seconds
                )
            await self.repo.collect_expired(now)
            record, token = self._new(now)
            cookie = token
        assert token is not None
        pending = (
            await self.session.get(LoginChallenge, record.token_hash) if user is None else None
        )
        state = self._state(
            record,
            token,
            user,
            mfa_required=bool(pending and pending.expires_at > now and pending.attempts < 5),
        )
        await self.session.commit()
        return SessionResult(state, cookie)

    async def _write_session(
        self, raw_token: str | None, supplied_csrf: str | None, *, lock: bool = True
    ) -> tuple[BrowserSession, User | None]:
        token = valid_session_token(raw_token)
        if token is None or not valid_csrf(token, supplied_csrf):
            raise AuthFailure(403, 40310, "登录状态已更新，请刷新后重试")
        record, user = await self._existing(token, datetime.now(UTC), lock=lock)
        if record is None:
            await self.session.commit()
            raise AuthFailure(403, 40310, "登录状态已更新，请刷新后重试")
        return record, user

    async def login(
        self,
        raw_token: str | None,
        supplied_csrf: str | None,
        username: str,
        password: str,
        peer: str,
    ) -> SessionResult:
        record, _ = await self._write_session(raw_token, supplied_csrf, lock=False)
        now = datetime.now(UTC)
        name = normalize_username(username)
        is_email = "@" in name
        ip_allowed = await self.repo.consume_limit(
            "login-ip:" + peer,
            self.config.login_attempts_per_ip,
            self.config.login_window_seconds,
            now,
        )
        # Lock the verified address before using its canonical username for rate limiting.
        user = None
        if ip_allowed:
            if is_email:
                user = await self.repo.user_by_verified_email(name, lock=True)
            else:
                user = await self.repo.user_by_name(name, lock=True)
        account_allowed = ip_allowed and await self.repo.consume_limit(
            "login-account:" + (user.username if user else name),
            self.config.login_attempts_per_account,
            self.config.login_window_seconds,
            now,
        )
        if not ip_allowed or not account_allowed:
            await self.session.commit()
            raise AuthFailure(
                429, 42901, "登录尝试过于频繁，请稍后再试", self.config.login_window_seconds
            )
        # Lock credentials before touching authenticated sessions. A concurrent
        # password reset/disable cannot be followed by a login using the old hash.
        encoded = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        valid = await to_thread.run_sync(
            verify_password, encoded, password, limiter=self.password_limiter
        )
        if user is None or not user.active or not valid:
            self.session.add(
                AuthAuditEvent(
                    action="login_failed", user_id=user.id if user else None, created_at=now
                )
            )
            await self.session.commit()
            raise AuthFailure(401, 40110, "账号或密码不正确")
        factor = await self.session.get(TwoFactorCredential, user.id)
        if factor is not None and factor.enabled_at is not None:
            from app.modules.auth.two_factor_service import CHALLENGE_TTL, cipher

            cipher(self.config)  # Fail closed if the encryption key is unavailable.
            await self.session.execute(
                delete(LoginChallenge).where(LoginChallenge.session_hash == record.token_hash)
            )
            record.user_id = None
            self.session.add(
                LoginChallenge(
                    session_hash=record.token_hash,
                    user_id=user.id,
                    auth_version=user.auth_version,
                    expires_at=now + CHALLENGE_TTL,
                    attempts=0,
                )
            )
            self.session.add(
                AuthAuditEvent(
                    action="login_second_factor_required", user_id=user.id, created_at=now
                )
            )
            result = SessionResult(
                self._state(record, valid_session_token(raw_token) or "", None, mfa_required=True)
            )
            await self.session.commit()
            return result
        await self.session.delete(record)
        new_record, token = self._new(now, user)
        self.session.add(AuthAuditEvent(action="login", user_id=user.id, created_at=now))
        result = SessionResult(self._state(new_record, token, user), token)
        await self.session.commit()
        return result

    async def logout(self, raw_token: str | None, supplied_csrf: str | None) -> SessionResult:
        record, user = await self._write_session(raw_token, supplied_csrf)
        now = datetime.now(UTC)
        await self.session.delete(record)
        new_record, token = self._new(now)
        self.session.add(
            AuthAuditEvent(action="logout", user_id=user.id if user else None, created_at=now)
        )
        result = SessionResult(self._state(new_record, token, None), token)
        await self.session.commit()
        return result
