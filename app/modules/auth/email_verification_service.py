"""Private, one-time email verification with Worker-owned SMTP delivery."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.accounts_schemas import (
    AccountProfile,
    EmailVerificationConfirm,
    EmailVerificationRequest,
    EmailVerificationState,
)
from app.modules.auth.accounts_service import profile
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_service import (
    default_mail_profile,
    mail_ready,
    stored_mail_profiles,
)

CODE_TTL = timedelta(minutes=15)


def code_digest(request_id: UUID, salt: str, code: str) -> str:
    return hashlib.sha256(f"{request_id}:{salt}:{code}".encode()).hexdigest()


def verification_state(row: EmailVerification) -> EmailVerificationState:
    return EmailVerificationState(
        request_id=row.id,
        email=row.email,
        status=cast(Any, row.status),
        expires_at=row.expires_at,
        retry_after_seconds=60,
    )


class EmailVerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def request(
        self, actor: User, payload: EmailVerificationRequest
    ) -> EmailVerificationState:
        previous = await self.session.get(EmailVerification, payload.operation_id)
        if previous:
            if previous.user_id != actor.id or previous.email != payload.email:
                raise AuthFailure(409, 40972, "验证请求编号已被使用")
            return verification_state(previous)
        now = datetime.now(UTC)
        latest = await self.session.scalar(
            select(EmailVerification)
            .where(
                EmailVerification.user_id == actor.id,
                EmailVerification.status.in_(["queued", "sending", "sent"]),
            )
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
        if latest and latest.created_at > now - timedelta(seconds=60):
            raise AuthFailure(429, 42972, "验证码发送过于频繁，请稍后再试")
        count = await self.session.scalar(
            select(func.count())
            .select_from(EmailVerification)
            .where(
                EmailVerification.user_id == actor.id,
                EmailVerification.created_at >= now - timedelta(hours=1),
            )
        )
        if count and count >= 5:
            raise AuthFailure(429, 42972, "验证码请求次数较多，请稍后再试")

        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        mail_revision, _ = stored_mail_profiles(data)
        mail = default_mail_profile(data)
        if mail is None or not mail.enabled or not mail_ready(mail.value, mail.password):
            raise AuthFailure(422, 42272, "请先配置并启用可用的 SMTP 邮件平台")

        await self.session.execute(
            update(EmailVerification)
            .where(
                EmailVerification.user_id == actor.id,
                EmailVerification.status.in_(["queued", "sending", "sent"]),
            )
            .values(status="expired", error="superseded", delivery_code="")
        )
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
        if row.status == "verified":
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
        user.email = payload.email
        user.email_verified_at = now
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
        await self.session.commit()
        return profile(user)
