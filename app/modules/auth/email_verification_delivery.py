"""Worker-only delivery for short-lived account email verification codes."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.progress import task_progress
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.models import User
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles
from app.modules.settings.mail_transport import send_notification


async def deliver(config: Settings) -> dict[str, Any]:
    database = Database(config.database.url.get_secret_value())
    try:
        now = datetime.now(UTC)
        async with database.session_factory() as session:
            await session.execute(
                delete(EmailVerification).where(
                    EmailVerification.created_at < now - timedelta(days=7)
                )
            )
            await session.execute(
                update(EmailVerification)
                .where(
                    EmailVerification.status.in_(["queued", "sending"]),
                    EmailVerification.expires_at <= now,
                )
                .values(status="expired", error="expired", delivery_code="")
            )
            await session.execute(
                update(EmailVerification)
                .where(
                    EmailVerification.status == "sending",
                    EmailVerification.lease_until < now,
                )
                .values(status="failed", error="interrupted", delivery_code="")
            )
            row = await session.scalar(
                select(EmailVerification)
                .where(
                    EmailVerification.status == "queued",
                    EmailVerification.expires_at > now,
                )
                .order_by(EmailVerification.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                await session.commit()
                return {"processed": 0}
            site = await session.get(SiteSettings, 1)
            data = site.data if site else {}
            revision, _ = stored_mail_profiles(data)
            profile = mail_profile(data, row.mail_profile_id)
            if (
                revision != row.mail_revision
                or profile is None
                or not profile.enabled
                or not mail_ready(profile.value, profile.password)
                or not row.delivery_code
            ):
                row.status = "failed"
                row.error = "email_not_configured"
                row.delivery_code = ""
                await session.commit()
                return {"processed": 1}
            value, password = profile.value, profile.password
            user = await session.get(User, row.user_id)
            if user is None or not user.active:
                row.status, row.error, row.delivery_code = (
                    "failed",
                    "recipient_unavailable",
                    "",
                )
                await session.commit()
                return {"processed": 1}
            lease_until = now + timedelta(seconds=150)
            row.status = "sending"
            row.lease_until = lease_until
            identity = row.id
            recipient = row.email
            code = row.delivery_code
            locale = user.preferred_locale
            await session.commit()

        await task_progress(stage="email-verification", message="Sending verification email")
        budget = min(120, max(15, value.timeout_seconds * 4)) + 5
        subject = (
            "验证你的邮箱 / Verify your email address"
            if locale.startswith("en")
            else "验证你的邮箱"
        )
        text = (
            "你的 Everunfold 邮箱验证码是："
            f"{code}\n\n验证码 15 分钟内有效。若不是你发起的，请忽略这封邮件。\n\n"
            "Your Everunfold email verification code is: "
            f"{code}\n\nIt expires in 15 minutes. Ignore this message if you did not request it."
        )
        try:
            status, error = await asyncio.wait_for(
                asyncio.to_thread(
                    send_notification,
                    value,
                    password,
                    recipient,
                    identity,
                    subject,
                    text,
                ),
                timeout=budget,
            )
        except TimeoutError:
            status, error = "unknown", "delivery_unknown"
        async with database.session_factory() as session:
            await session.execute(
                update(EmailVerification)
                .where(
                    EmailVerification.id == identity,
                    EmailVerification.status == "sending",
                )
                .values(
                    status="sent" if status == "sent" else "failed",
                    error=error,
                    delivery_code="",
                    sent_at=datetime.now(UTC) if status == "sent" else None,
                    lease_until=None,
                )
            )
            await session.commit()
        return {"processed": 1}
    finally:
        await database.close()
