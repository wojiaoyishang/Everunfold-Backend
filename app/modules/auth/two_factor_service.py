"""Encrypted TOTP, single-use recovery codes and password-bound login challenges."""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AuthConfig
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import (
    AuthAuditEvent,
    BrowserSession,
    LoginChallenge,
    TwoFactorCredential,
    User,
)
from app.modules.auth.security import token_digest
from app.modules.auth.service import AuthFailure, AuthService, SessionResult
from app.modules.auth.two_factor_schemas import TwoFactorActivated, TwoFactorSetup, TwoFactorState

CHALLENGE_TTL = timedelta(minutes=5)
SETUP_TTL = timedelta(minutes=10)


def cipher(config: AuthConfig) -> Fernet:
    master = os.environ.get(config.totp_master_key_env, "")
    if not 43 <= len(master) <= 256:
        raise AuthFailure(503, 50377, "两步验证密钥尚未配置")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"everunfold.totp.v1\0" + master.encode()).digest()
    )
    return Fernet(key)


def totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.digest(key, struct.pack(">Q", counter), "sha1")
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFF_FFFF
    return f"{value % 1_000_000:06d}"


def recovery_hash(code: str) -> str:
    return hashlib.sha256(b"everunfold.recovery.v1\0" + code.encode()).hexdigest()


def new_recovery_codes() -> tuple[list[str], list[str]]:
    codes = [secrets.token_urlsafe(18) for _ in range(10)]
    return codes, [recovery_hash(code) for code in codes]


class TwoFactorService:
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth
        self.session: AsyncSession = auth.session

    async def credential(self, user_id: UUID, *, lock: bool = False) -> TwoFactorCredential | None:
        statement = select(TwoFactorCredential).where(TwoFactorCredential.user_id == user_id)
        return await self.session.scalar(statement.with_for_update() if lock else statement)

    async def _attempt(self, actor: User) -> None:
        allowed = await self.auth.repo.consume_limit(
            "two-factor:" + str(actor.id),
            self.auth.config.login_attempts_per_account,
            self.auth.config.login_window_seconds,
            datetime.now(UTC),
        )
        if not allowed:
            await self.session.commit()
            raise AuthFailure(429, 42901, "尝试次数较多，请稍后再试")

    async def state(self, actor: User) -> TwoFactorState:
        row = await self.credential(actor.id)
        return TwoFactorState(
            enabled=bool(row and row.enabled_at),
            enabled_at=row.enabled_at if row else None,
            recovery_codes_remaining=len(row.recovery_hashes) if row and row.enabled_at else 0,
            available=43 <= len(os.environ.get(self.auth.config.totp_master_key_env, "")) <= 256,
        )

    async def begin(self, actor: User, password: str) -> TwoFactorSetup:
        crypto = cipher(self.auth.config)
        await AccountsService(self.session, self.auth).confirm_password(actor, password)
        row = await self.credential(actor.id, lock=True)
        if row and row.enabled_at:
            raise AuthFailure(409, 40977, "两步验证已启用")
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        now = datetime.now(UTC)
        if row is None:
            row = TwoFactorCredential(user_id=actor.id, secret_ciphertext="")
            self.session.add(row)
        row.secret_ciphertext = crypto.encrypt(secret.encode()).decode()
        row.setup_expires_at = now + SETUP_TTL
        row.last_counter = -1
        row.recovery_hashes = []
        await self.session.commit()
        uri = (
            f"otpauth://totp/{quote('Everunfold:' + actor.username)}?"
            f"secret={secret}&issuer=Everunfold&algorithm=SHA1&digits=6&period=30"
        )
        return TwoFactorSetup(secret=secret, otpauth_uri=uri, expires_at=row.setup_expires_at)

    def _secret(self, row: TwoFactorCredential) -> str:
        try:
            return cipher(self.auth.config).decrypt(row.secret_ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise AuthFailure(503, 50377, "两步验证密钥不可用") from exc

    def _verify(self, row: TwoFactorCredential, code: str, *, allow_recovery: bool) -> bool:
        if len(code) == 6 and code.isascii() and code.isdigit():
            secret = self._secret(row)
            current = int(time.time()) // 30
            for counter in (current - 1, current, current + 1):
                if counter > row.last_counter and hmac.compare_digest(totp(secret, counter), code):
                    row.last_counter = counter
                    return True
        if allow_recovery and 20 <= len(code) <= 32:
            digest = recovery_hash(code)
            for index, stored in enumerate(row.recovery_hashes):
                if hmac.compare_digest(stored, digest):
                    row.recovery_hashes = [
                        *row.recovery_hashes[:index],
                        *row.recovery_hashes[index + 1 :],
                    ]
                    return True
        return False

    async def activate(self, actor: User, code: str, current_token: str) -> TwoFactorActivated:
        actor = await AccountsService(self.session, self.auth).target(actor.id, lock=True)
        await self._attempt(actor)
        row = await self.credential(actor.id, lock=True)
        if (
            not row
            or row.enabled_at
            or not row.setup_expires_at
            or row.setup_expires_at < datetime.now(UTC)
        ):
            raise AuthFailure(409, 40977, "请重新开始两步验证设置")
        if not self._verify(row, code, allow_recovery=False):
            await self.session.commit()
            raise AuthFailure(422, 42277, "验证码不正确")
        codes, hashes = new_recovery_codes()
        row.recovery_hashes = hashes
        row.enabled_at = datetime.now(UTC)
        row.setup_expires_at = None
        actor.auth_version += 1
        await self._revoke_others(actor, current_token)
        self._audit(actor, "two_factor_enabled")
        await self.session.commit()
        return TwoFactorActivated(recovery_codes=codes)

    async def change(
        self, actor: User, password: str, code: str, current_token: str, *, disable: bool
    ) -> TwoFactorActivated | TwoFactorState:
        actor = await AccountsService(self.session, self.auth).target(actor.id, lock=True)
        await AccountsService(self.session, self.auth).confirm_password(actor, password)
        await self._attempt(actor)
        row = await self.credential(actor.id, lock=True)
        if not row or not row.enabled_at:
            raise AuthFailure(409, 40977, "两步验证尚未启用")
        if not self._verify(row, code, allow_recovery=True):
            await self.session.commit()
            raise AuthFailure(422, 42277, "验证码或恢复码不正确")
        await self._revoke_others(actor, current_token)
        if disable:
            actor.auth_version += 1
            await self.session.delete(row)
            self._audit(actor, "two_factor_disabled")
            await self.session.commit()
            return TwoFactorState(
                enabled=False, enabled_at=None, recovery_codes_remaining=0, available=True
            )
        codes, row.recovery_hashes = new_recovery_codes()
        actor.auth_version += 1
        self._audit(actor, "two_factor_recovery_rotated")
        await self.session.commit()
        return TwoFactorActivated(recovery_codes=codes)

    async def _revoke_others(self, actor: User, token: str) -> None:
        await self.session.execute(
            delete(BrowserSession).where(
                BrowserSession.user_id == actor.id, BrowserSession.token_hash != token_digest(token)
            )
        )

    def _audit(self, actor: User, action: str) -> None:
        self.session.add(
            AuthAuditEvent(
                user_id=actor.id,
                target_user_id=actor.id,
                action=action,
                created_at=datetime.now(UTC),
            )
        )

    async def complete_login(
        self, raw_token: str | None, csrf: str | None, code: str
    ) -> SessionResult:
        record, current = await self.auth._write_session(raw_token, csrf)
        if current is not None:
            raise AuthFailure(409, 40977, "已登录，请先退出")
        row = await self.session.scalar(
            select(LoginChallenge)
            .where(LoginChallenge.session_hash == record.token_hash)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if not row or row.expires_at <= now or row.attempts >= 5:
            raise AuthFailure(401, 40177, "两步验证已过期，请重新登录")
        user = await self.session.scalar(
            select(User).where(User.id == row.user_id).with_for_update()
        )
        credential = await self.credential(row.user_id, lock=True)
        if (
            not user
            or not user.active
            or user.auth_version != row.auth_version
            or not credential
            or not credential.enabled_at
        ):
            await self.session.delete(row)
            await self.session.commit()
            raise AuthFailure(401, 40177, "登录状态已失效，请重新登录")
        row.attempts += 1
        if not self._verify(credential, code, allow_recovery=True):
            await self.session.commit()
            raise AuthFailure(401, 40177, "验证码或恢复码不正确")
        await self.session.delete(row)
        await self.session.flush()
        await self.session.delete(record)
        new_record, token = self.auth._new(now, user)
        self._audit(user, "login")
        result = SessionResult(self.auth._state(new_record, token, user), token)
        await self.session.commit()
        return result
