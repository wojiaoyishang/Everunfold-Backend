"""Worker-only delivery for short-lived account email verification codes."""

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.progress import task_progress
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.email_verification_service import is_verified_address
from app.modules.auth.models import User
from app.modules.auth.security_delivery import send_security_mail, verification_message
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles


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
            user = await session.get(User, row.user_id)
            if user is None or not user.active or user.revision != row.profile_revision:
                row.status, row.error, row.delivery_code = (
                    "failed",
                    "recipient_unavailable",
                    "",
                )
                await session.commit()
                return {"processed": 1}
            if is_verified_address(user, row.email):
                row.status, row.error, row.delivery_code = "expired", "already_verified", ""
                await session.commit()
                return {"processed": 1}
            lease_until = now + timedelta(seconds=150)
            row.status = "sending"
            row.lease_until = lease_until
            identity = row.id
            recipient = row.email
            message = await verification_message(
                session,
                config,
                user.preferred_locale,
                row.delivery_code,
                max(1, ceil((row.expires_at - now).total_seconds() / 60)),
                display_name=user.display_name,
            )
            await session.commit()

        await task_progress(stage="email-verification", message="Sending verification email")
        status, error = await send_security_mail(profile, recipient, identity, message)
        async with database.session_factory() as session:
            await session.execute(
                update(EmailVerification)
                .where(
                    EmailVerification.id == identity,
                    EmailVerification.status == "sending",
                )
                .values(
                    status="sent" if status == "sent" else "failed",
                    error="delivery_unknown" if status == "unknown" else error,
                    delivery_code="",
                    sent_at=datetime.now(UTC) if status == "sent" else None,
                    lease_until=None,
                )
            )
            await session.commit()
        return {"processed": 1}
    finally:
        await database.close()
