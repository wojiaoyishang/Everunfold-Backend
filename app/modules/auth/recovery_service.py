"""Request and consume an anonymous browser-bound password recovery code."""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from anyio import to_thread
from sqlalchemy import delete, select

from app.modules.auth.callback_events import invalidate_user_keys
from app.modules.auth.models import AuthAuditEvent, BrowserSession, User
from app.modules.auth.recovery_models import PasswordRecovery
from app.modules.auth.recovery_schemas import RecoveryConfirm, RecoveryRequest
from app.modules.auth.security import hash_password, normalize_username, token_digest
from app.modules.auth.security_settings import configured_sender, recovery_policy
from app.modules.auth.service import AuthFailure, AuthService
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import PASSWORD_RECOVERY, PASSWORD_RESET_COMPLETED
from app.modules.notifications.routing import auth_code_mail, auth_mail_route
from app.modules.settings.mail_service import mail_ready, stored_mail_profiles

RECOVERY_TTL = timedelta(minutes=30)
MAX_ATTEMPTS = 5


def recovery_user(row: PasswordRecovery, user: User | None) -> bool:
    return bool(
        user
        and user.active
        and user.email_verified_at is not None
        and user.email == row.email
        and user.revision == row.user_revision
        and user.auth_version == row.auth_version
    )


class RecoveryService:
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth
        self.session = auth.session

    async def request(
        self, token: str | None, csrf: str | None, payload: RecoveryRequest, peer: str
    ) -> None:
        browser, authenticated = await self.auth._write_session(token, csrf)
        if authenticated:
            raise AuthFailure(409, 40977, "请先退出当前账号")
        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        enabled, epoch = recovery_policy(data)
        if not enabled:
            raise AuthFailure(403, 40380, "站点未开放找回密码")
        mail = await auth_code_mail(self.session, data, PASSWORD_RECOVERY, configured_sender(data))
        if not mail or not mail.enabled or not mail_ready(mail.value, mail.password):
            raise AuthFailure(503, 50380, "找回密码邮件暂不可用")
        now = datetime.now(UTC)
        for key, limit, window in (
            ("recovery-ip:" + peer, 15, 3600),
            ("recovery-browser:" + browser.token_hash, 1, 60),
        ):
            if not await self.auth.repo.consume_limit(key, limit, window, now):
                await self.session.commit()
                raise AuthFailure(429, 42980, "找回密码请求过于频繁，请稍后再试", window)
        identifier = normalize_username(payload.identifier)
        allowed = await self.auth.repo.consume_limit(
            "recovery-identifier:" + identifier, 4, 3600, now
        )
        user = (
            await self.auth.repo.user_by_verified_email(identifier)
            if "@" in identifier
            else await self.auth.repo.user_by_name(identifier)
        )
        rate_identity = str(user.id) if user else "missing:" + identifier
        user_allowed = await self.auth.repo.consume_limit(
            "recovery-user:" + rate_identity, 4, 3600, now
        )
        if (
            user is None
            or not user.active
            or not user.email
            or user.email_verified_at is None
            or not allowed
            or not user_allowed
        ):
            user = None
        await self.session.execute(
            delete(PasswordRecovery).where(
                PasswordRecovery.session_hash == browser.token_hash,
                PasswordRecovery.status.in_(("queued", "sending", "sent", "unknown", "failed")),
            )
        )
        code = secrets.token_urlsafe(24)
        self.session.add(
            PasswordRecovery(
                id=uuid4(),
                session_hash=browser.token_hash,
                user_id=user.id if user else None,
                user_revision=user.revision if user else 0,
                auth_version=user.auth_version if user else 0,
                policy_epoch=epoch,
                email=user.email if user else "",
                mail_profile_id=str(mail.id),
                mail_revision=stored_mail_profiles(data)[0],
                code_hash=token_digest(code),
                delivery_code=code if user else "",
                status="queued" if user else "failed",
                attempts=0,
                created_at=now,
                expires_at=min(now + RECOVERY_TTL, browser.expires_at),
            )
        )
        await self.session.commit()

    async def confirm(
        self, token: str | None, csrf: str | None, payload: RecoveryConfirm, peer: str
    ) -> None:
        browser, authenticated = await self.auth._write_session(token, csrf)
        site = await self.session.get(SiteSettings, 1, with_for_update=True)
        enabled, epoch = recovery_policy(site.data if site else {})
        if not enabled:
            raise AuthFailure(403, 40380, "站点未开放找回密码")
        now = datetime.now(UTC)
        if not await self.auth.repo.consume_limit("recovery-confirm:" + peer, 30, 3600, now):
            await self.session.commit()
            raise AuthFailure(429, 42980, "找回密码请求过于频繁，请稍后再试", 3600)
        row = await self.session.scalar(
            select(PasswordRecovery)
            .where(
                PasswordRecovery.session_hash == browser.token_hash,
                PasswordRecovery.status.in_(("sent", "unknown")),
                PasswordRecovery.expires_at > now,
                PasswordRecovery.policy_epoch == epoch,
                PasswordRecovery.attempts < MAX_ATTEMPTS,
            )
            .order_by(PasswordRecovery.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        invalid = AuthFailure(422, 42280, "重置码无效或已过期")
        if authenticated or not row or not row.user_id:
            await self.session.commit()
            raise invalid
        if not hmac.compare_digest(row.code_hash, token_digest(payload.code)):
            row.attempts += 1
            await self.session.commit()
            raise invalid
        user = await self.session.get(User, row.user_id, with_for_update=True)
        if not user or not recovery_user(row, user):
            row.status = "failed"
            await self.session.commit()
            raise invalid
        user.password_hash = await to_thread.run_sync(
            hash_password, payload.new_password, limiter=self.auth.password_limiter
        )
        user.must_change_password = False
        user.auth_version += 1
        user.key_epoch += 1
        user.revision += 1
        user.updated_at = now
        await self.session.execute(delete(BrowserSession).where(BrowserSession.user_id == user.id))
        await invalidate_user_keys(self.session, user)
        self.session.add(
            AuthAuditEvent(
                action="password_recovered", user_id=user.id, target_user_id=user.id, created_at=now
            )
        )
        row.code_hash = ""
        row.delivery_code = ""
        notice = await auth_mail_route(self.session, PASSWORD_RESET_COMPLETED)
        row.status = "notice_queued" if notice.enabled else "done"
        if notice.enabled and notice.profile_id is not None:
            row.mail_profile_id = notice.profile_id
            row.mail_revision = stored_mail_profiles(site.data if site else {})[0]
        row.user_revision = user.revision
        row.auth_version = user.auth_version
        row.expires_at = now + timedelta(hours=1)
        await self.session.commit()
