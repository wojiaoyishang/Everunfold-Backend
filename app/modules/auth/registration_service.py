"""Browser-bound registration and one-time invitation use."""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import cast
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.modules.auth.email_verification_service import CODE_TTL, SEND_COOLDOWN, code_digest
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.registration_models import PendingRegistration, RegistrationInvite
from app.modules.auth.registration_schemas import (
    InvitationBatchCreated,
    InvitationCreated,
    InvitationItem,
    InvitationList,
    RegistrationConfirm,
    RegistrationOptions,
    RegistrationRequest,
    RegistrationState,
    RegistrationStatus,
)
from app.modules.auth.security import (
    hash_password,
    normalize_username,
    token_digest,
    valid_session_token,
)
from app.modules.auth.security_settings import configured_sender, registration_mode
from app.modules.auth.service import AuthFailure, AuthService, SessionResult
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import REGISTRATION_SUCCEEDED, REGISTRATION_VERIFICATION
from app.modules.notifications.events import publish_account_created
from app.modules.notifications.routing import auth_code_mail
from app.modules.settings.mail_service import mail_ready, stored_mail_profiles


def state(row: PendingRegistration, now: datetime) -> RegistrationState:
    status = "expired" if row.expires_at <= now else row.status
    return RegistrationState(
        request_id=row.id,
        status=cast(RegistrationStatus, status),
        expires_at=row.expires_at,
        retry_after_seconds=max(0, ceil((row.created_at + SEND_COOLDOWN - now).total_seconds())),
    )


def available(invite: RegistrationInvite | None, now: datetime) -> bool:
    return bool(
        invite
        and (invite.expires_at is None or invite.expires_at > now)
        and not invite.consumed_at
        and not invite.revoked_at
    )


class RegistrationService:
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth
        self.session = auth.session

    async def options(self) -> RegistrationOptions:
        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        mail = await auth_code_mail(
            self.session, data, REGISTRATION_VERIFICATION, configured_sender(data)
        )
        return RegistrationOptions(
            mode=registration_mode(data),
            mail_ready=bool(mail and mail.enabled and mail_ready(mail.value, mail.password)),
        )

    async def request(
        self, token: str | None, csrf: str | None, payload: RegistrationRequest, peer: str
    ) -> RegistrationState:
        browser, user = await self.auth._write_session(token, csrf)
        if user:
            raise AuthFailure(409, 40978, "请先退出当前账号")
        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        mode = registration_mode(data)
        if mode == "closed":
            raise AuthFailure(403, 40378, "站点当前不接受注册")
        now = datetime.now(UTC)
        name = normalize_username(payload.username)
        invite_token = valid_session_token((payload.invite_code or "").strip())
        for key, limit, window in (
            ("registration-ip:" + peer, 12, 3600),
            ("registration-email:" + payload.email, 4, 3600),
            ("registration-name:" + name, 8, 3600),
        ):
            if not await self.auth.repo.consume_limit(key, limit, window, now):
                await self.session.commit()
                raise AuthFailure(429, 42978, "注册请求过于频繁，请稍后再试", window)
        invite = None
        if mode == "invite":
            if invite_token:
                invite_hash = token_digest(invite_token)
                if not await self.auth.repo.consume_limit(
                    "registration-invite:" + invite_hash, 8, 3600, now
                ):
                    await self.session.commit()
                    raise AuthFailure(429, 42978, "注册请求过于频繁，请稍后再试", 3600)
                invite = await self.session.scalar(
                    select(RegistrationInvite).where(RegistrationInvite.token_hash == invite_hash)
                )
            if not available(invite, now):
                await self.session.commit()
                raise AuthFailure(422, 42278, "邀请码无效或已过期")
        latest = await self.session.scalar(
            select(PendingRegistration)
            .where(PendingRegistration.session_hash == browser.token_hash)
            .order_by(PendingRegistration.created_at.desc())
            .limit(1)
        )
        if latest and latest.created_at + SEND_COOLDOWN > now:
            await self.session.commit()
            raise AuthFailure(
                429, 42978, "请等待后再发送验证码", state(latest, now).retry_after_seconds
            )
        mail = await auth_code_mail(
            self.session, data, REGISTRATION_VERIFICATION, configured_sender(data)
        )
        if not mail or not mail.enabled or not mail_ready(mail.value, mail.password):
            await self.session.commit()
            raise AuthFailure(503, 50378, "注册邮件暂不可用")
        password = await to_thread.run_sync(
            hash_password, payload.password, limiter=self.auth.password_limiter
        )
        await self.session.execute(
            delete(PendingRegistration).where(
                PendingRegistration.session_hash == browser.token_hash
            )
        )
        identity, salt = uuid4(), secrets.token_hex(16)
        code = f"{secrets.randbelow(1_000_000):06d}"
        row = PendingRegistration(
            id=identity,
            session_hash=browser.token_hash,
            username=name,
            display_name=payload.display_name,
            email=payload.email,
            password_hash=password,
            locale=payload.locale,
            invite_id=invite.id if invite else None,
            salt=salt,
            code_hash=code_digest(identity, salt, code),
            delivery_code=code,
            mail_profile_id=str(mail.id),
            mail_revision=stored_mail_profiles(data)[0],
            status="queued",
            created_at=now,
            expires_at=min(now + CODE_TTL, browser.expires_at),
        )
        self.session.add(row)
        result = state(row, now)
        await self.session.commit()
        return result

    async def status(self, token: str | None) -> RegistrationState:
        token = valid_session_token(token)
        now = datetime.now(UTC)
        browser = await self.auth.repo.browser_session(token, now, lock=False) if token else None
        if not browser:
            raise AuthFailure(404, 40478, "注册请求不存在或已失效")
        row = await self.session.scalar(
            select(PendingRegistration)
            .where(PendingRegistration.session_hash == browser.token_hash)
            .order_by(PendingRegistration.created_at.desc())
            .limit(1)
        )
        if not row:
            raise AuthFailure(404, 40478, "注册请求不存在或已失效")
        return state(row, now)

    async def confirm(
        self, token: str | None, csrf: str | None, payload: RegistrationConfirm
    ) -> SessionResult:
        browser, user = await self.auth._write_session(token, csrf)
        row = await self.session.scalar(
            select(PendingRegistration)
            .where(
                PendingRegistration.id == payload.request_id,
                PendingRegistration.session_hash == browser.token_hash,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        if (
            user
            or not row
            or row.expires_at <= now
            or row.status not in {"sent", "unknown"}
            or row.attempts >= 5
        ):
            raise AuthFailure(422, 42279, "验证码无效或已过期")
        if not hmac.compare_digest(row.code_hash, code_digest(row.id, row.salt, payload.code)):
            row.attempts += 1
            if row.attempts >= 5:
                row.status = "expired"
            await self.session.commit()
            raise AuthFailure(422, 42279, "验证码无效或已过期")
        site = await self.session.get(SiteSettings, 1)
        mode = registration_mode(site.data if site else {})
        if mode == "closed" or (mode == "invite" and row.invite_id is None):
            raise AuthFailure(403, 40378, "当前注册策略已更改，请重新开始")
        if row.invite_id:
            invite = await self.session.get(RegistrationInvite, row.invite_id, with_for_update=True)
            if not invite or not available(invite, now):
                raise AuthFailure(422, 42278, "邀请码无效或已过期")
            invite.consumed_at = now
        if await self.session.scalar(
            select(User.id).where(
                or_(User.username == row.username, func.lower(func.btrim(User.email)) == row.email)
            )
        ):
            raise AuthFailure(409, 40978, "用户名或邮箱已被使用")
        user = User(
            username=row.username,
            display_name=row.display_name,
            email=row.email,
            email_verified_at=now,
            password_hash=row.password_hash,
            preferred_locale=row.locale,
            role="user",
            grants={},
            active=True,
            created_at=now,
            updated_at=now,
        )
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthFailure(409, 40978, "用户名或邮箱已被使用") from exc
        self.session.add(
            AuthAuditEvent(
                action="user_registered", user_id=user.id, target_user_id=user.id, created_at=now
            )
        )
        await publish_account_created(self.session, REGISTRATION_SUCCEEDED, user, user.id)
        await self.session.delete(row)
        await self.session.delete(browser)
        active, session_token = self.auth._new(now, user)
        result = SessionResult(self.auth._state(active, session_token, user), session_token)
        await self.session.commit()
        return result

    async def invitations(self) -> InvitationList:
        rows = await self.session.scalars(
            select(RegistrationInvite)
            .order_by(RegistrationInvite.created_at.desc(), RegistrationInvite.id)
            .limit(100)
        )
        return InvitationList(
            items=[
                InvitationItem(
                    id=row.id,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    consumed_at=row.consumed_at,
                    revoked_at=row.revoked_at,
                )
                for row in rows
            ]
        )

    async def invite(self, actor: User, days: int) -> InvitationCreated:
        batch = await self.invite_batch(actor, 1, datetime.now(UTC) + timedelta(days=days))
        return batch.items[0]

    async def invite_batch(
        self, actor: User, count: int, expires_at: datetime | None
    ) -> InvitationBatchCreated:
        now = datetime.now(UTC)
        if expires_at is not None and expires_at <= now:
            raise AuthFailure(422, 42278, "邀请码到期时间必须晚于当前时间")

        items = []
        for _ in range(count):
            code = secrets.token_urlsafe(32)
            row = RegistrationInvite(
                id=uuid4(),
                token_hash=token_digest(code),
                created_by=actor.id,
                created_at=now,
                expires_at=expires_at,
            )
            self.session.add(row)
            self.session.add(
                AuthAuditEvent(
                    action="registration_invite_created",
                    user_id=actor.id,
                    created_at=now,
                    details={"invite_id": str(row.id)},
                )
            )
            items.append(InvitationCreated(id=row.id, code=code, expires_at=expires_at))
        await self.session.commit()
        return InvitationBatchCreated(items=items)

    async def revoke(self, actor: User, identity: UUID) -> InvitationList:
        row = await self.session.get(RegistrationInvite, identity, with_for_update=True)
        if not row:
            raise AuthFailure(404, 40478, "邀请码不存在")
        if not row.revoked_at and not row.consumed_at:
            now = datetime.now(UTC)
            row.revoked_at = now
            self.session.add(
                AuthAuditEvent(
                    action="registration_invite_revoked",
                    user_id=actor.id,
                    created_at=now,
                    details={"invite_id": str(row.id)},
                )
            )
        await self.session.commit()
        return await self.invitations()
