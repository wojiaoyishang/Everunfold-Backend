"""Private, one-time email verification with Worker-owned SMTP delivery."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.accounts_schemas import (
    AccountProfile,
    EmailVerificationConfirm,
    EmailVerificationRequest,
    EmailVerificationState,
    EmailVerificationStatus,
)
from app.modules.auth.accounts_service import profile
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import EMAIL_VERIFICATION, EMAIL_VERIFIED
from app.modules.notifications.events import publish_account_security
from app.modules.notifications.routing import auth_code_mail
from app.modules.settings.mail_service import (
    mail_ready,
    stored_mail_profiles,
)

CODE_TTL = timedelta(minutes=15)
SEND_COOLDOWN = timedelta(seconds=60)
SEND_WINDOW = timedelta(hours=1)
ACTIVE_STATES = ("queued", "sending", "sent")


def code_digest(request_id: UUID, salt: str, code: str) -> str:
    return hashlib.sha256(f"{request_id}:{salt}:{code}".encode()).hexdigest()


def verification_state(row: EmailVerification) -> EmailVerificationState:
    now = datetime.now(UTC)
    status = row.status
    if status in ACTIVE_STATES and row.expires_at <= now:
        status = "expired"
    elif status == "failed" and row.error in {"interrupted", "delivery_unknown"}:
        status = "unknown"
    elif status == "failed" and row.error == "too_many_attempts":
        status = "expired"
    return EmailVerificationState(
        request_id=row.id,
        email=row.email,
        status=cast(EmailVerificationStatus, status),
        expires_at=row.expires_at,
        retry_after_seconds=max(0, ceil((row.created_at + SEND_COOLDOWN - now).total_seconds())),
    )


class EmailVerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def status(self, actor: User, request_id: UUID) -> EmailVerificationState:
        row = await self.session.get(EmailVerification, request_id)
        if row is None or row.user_id != actor.id:
            raise AuthFailure(404, 40473, "验证请求不存在")
        return verification_state(row)

    async def replay(
        self, actor: User, payload: EmailVerificationRequest
    ) -> EmailVerificationState | None:
        previous = await self.session.get(EmailVerification, payload.operation_id)
        if previous is None:
            return None
        if previous.user_id != actor.id or previous.email != payload.email:
            raise AuthFailure(409, 40972, "验证请求编号已被使用")
        return verification_state(previous)

    async def request(
        self, actor: User, payload: EmailVerificationRequest
    ) -> EmailVerificationState:
        if previous := await self.replay(actor, payload):
            return previous
        now = datetime.now(UTC)
        latest = await self.session.scalar(
            select(EmailVerification)
            .where(EmailVerification.user_id == actor.id)
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
        if latest and latest.created_at > now - SEND_COOLDOWN:
            raise AuthFailure(
                429,
                42972,
                "验证码发送过于频繁，请稍后再试",
                verification_state(latest).retry_after_seconds,
            )
        count, oldest = (
            await self.session.execute(
                select(func.count(), func.min(EmailVerification.created_at))
                .select_from(EmailVerification)
                .where(
                    EmailVerification.user_id == actor.id,
                    EmailVerification.created_at > now - SEND_WINDOW,
                )
            )
        ).one()
        if count >= 5 and oldest is not None:
            raise AuthFailure(
                429,
                42972,
                "验证码请求次数较多，请稍后再试",
                ceil((oldest + SEND_WINDOW - now).total_seconds()),
            )

        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        mail_revision, _ = stored_mail_profiles(data)
        from app.modules.auth.security_settings import configured_sender

        mail = await auth_code_mail(self.session, data, EMAIL_VERIFICATION, configured_sender(data))
        if mail is None or not mail.enabled or not mail_ready(mail.value, mail.password):
            raise AuthFailure(422, 42272, "请先配置并启用可用的 SMTP 邮件平台")

        await invalidate_email_requests(self.session, actor.id, "superseded")
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        row = EmailVerification(
            id=payload.operation_id,
            user_id=actor.id,
            email=payload.email,
            salt=salt,
            code_hash=code_digest(payload.operation_id, salt, code),
            delivery_code=code,
            mail_profile_id=str(mail.id),
            status="queued",
            attempts=0,
            mail_revision=mail_revision,
            profile_revision=actor.revision,
            created_at=now,
            expires_at=now + CODE_TTL,
        )
        self.session.add(row)
        self.session.add(
            AuthAuditEvent(
                user_id=actor.id,
                action="email_verification_requested",
                details={"request_id": str(row.id)},
                created_at=now,
            )
        )
        await self.session.commit()
        return verification_state(row)

    async def confirm(self, actor: User, payload: EmailVerificationConfirm) -> AccountProfile:
        row = await self.session.scalar(
            select(EmailVerification)
            .where(
                EmailVerification.id == payload.request_id,
                EmailVerification.user_id == actor.id,
            )
            .with_for_update()
        )
        if row is None or row.email != payload.email:
            raise AuthFailure(422, 42273, "验证码无效或已过期")
        if row.status == "verified" and actor.email == row.email and actor.email_verified_at:
            return profile(actor)
        now = datetime.now(UTC)
        if row.expires_at <= now:
            row.status, row.error, row.delivery_code = "expired", "expired", ""
            await self.session.commit()
            raise AuthFailure(422, 42273, "验证码已过期，请重新发送")
        if row.status in {"queued", "sending"}:
            raise AuthFailure(409, 40973, "验证码仍在发送，请稍后重试")
        if row.status != "sent" or row.attempts >= 5:
            raise AuthFailure(422, 42273, "验证码无效或已过期，请重新发送")
        if row.profile_revision != actor.revision:
            raise AuthFailure(409, 40974, "账号资料已修改，请重新发起邮箱验证")
        if not hmac.compare_digest(
            row.code_hash,
            code_digest(row.id, row.salt, payload.code),
        ):
            row.attempts += 1
            if row.attempts >= 5:
                row.status, row.error, row.delivery_code = "failed", "too_many_attempts", ""
            await self.session.commit()
            raise AuthFailure(422, 42273, "验证码不正确")

        user = await self.session.scalar(select(User).where(User.id == actor.id).with_for_update())
        if user is None:
            raise AuthFailure(404, 40472, "账号不存在")
        claimed = await self.session.scalar(
            select(User.id)
            .where(
                User.id != user.id,
                func.lower(func.btrim(User.email)) == func.lower(payload.email),
            )
            .limit(1)
        )
        if claimed is not None:
            raise AuthFailure(409, 40975, "邮箱无法绑定，请使用其他邮箱")
        user.email = payload.email
        user.email_verified_at = now
        user.email_reviewed_at = None
        user.revision += 1
        user.updated_at = now
        row.status, row.verified_at, row.delivery_code = "verified", now, ""
        self.session.add(
            AuthAuditEvent(
                user_id=user.id,
                target_user_id=user.id,
                action="email_verified",
                details={"email_verified": True},
                created_at=now,
            )
        )
        await publish_account_security(self.session, EMAIL_VERIFIED, user, row.id)
        await self.session.commit()
        return profile(user)


async def invalidate_email_requests(session: AsyncSession, user_id: UUID, reason: str) -> None:
    await session.execute(
        update(EmailVerification)
        .where(EmailVerification.user_id == user_id, EmailVerification.status.in_(ACTIVE_STATES))
        .values(status="expired", error=reason, delivery_code="")
    )
