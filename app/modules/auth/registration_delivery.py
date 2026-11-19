"""Worker delivery of registration email verification codes."""

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.progress import task_progress
from app.core.plugins.business_events import dispatch, subscribers
from app.modules.auth.registration_models import PendingRegistration, RegistrationInvite
from app.modules.auth.registration_service import available
from app.modules.auth.security_delivery import send_security_mail, verification_message
from app.modules.auth.security_settings import registration_mode
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import REGISTRATION_VERIFICATION
from app.modules.notifications.observability import delivery_event
from app.modules.notifications.routing import EMAIL
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles


async def deliver(config: Settings) -> dict[str, Any]:
    database = Database(config.database.url.get_secret_value())
    try:
        now = datetime.now(UTC)
        async with database.session_factory() as session:
            await session.execute(
                delete(PendingRegistration).where(PendingRegistration.expires_at <= now)
            )
            await session.execute(
                update(PendingRegistration)
                .where(
                    PendingRegistration.status == "sending", PendingRegistration.lease_until <= now
                )
                .values(status="unknown", delivery_code="", lease_until=None)
            )
            row = await session.scalar(
                select(PendingRegistration)
                .where(PendingRegistration.status == "queued", PendingRegistration.expires_at > now)
                .order_by(PendingRegistration.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not row:
                await session.commit()
                return {"processed": 0}
            site = await session.get(SiteSettings, 1)
            data = site.data if site else {}
            handlers = subscribers(data)
            mail = mail_profile(data, row.mail_profile_id)
            invite = await session.get(RegistrationInvite, row.invite_id) if row.invite_id else None
            mode = registration_mode(data)
            mail_available = (
                mail is not None
                and mail.enabled
                and mail_ready(mail.value, mail.password)
                and stored_mail_profiles(data)[0] == row.mail_revision
            )
            if (
                not mail_available
                or mode == "closed"
                or ((mode == "invite" or row.invite_id is not None) and not available(invite, now))
                or not row.delivery_code
            ):
                row.status, row.delivery_code = "failed", ""
                await session.commit()
                dispatch(
                    handlers,
                    delivery_event(
                        "suppressed" if mail_available else "failed",
                        EMAIL,
                        row.id,
                        kind=REGISTRATION_VERIFICATION,
                        stage="security_mail",
                        recipient=row.email,
                        mail_profile_id=row.mail_profile_id,
                        reason="request_unavailable" if mail_available else "email_not_configured",
                    ),
                )
                return {"processed": 1}
            assert mail is not None
            row.status = "sending"
            row.lease_until = now + timedelta(seconds=150)
            identity, recipient = row.id, row.email
            minutes = max(1, ceil((row.expires_at - now).total_seconds() / 60))
            message = await verification_message(
                session,
                config,
                row.locale,
                row.delivery_code,
                minutes,
                display_name=row.display_name,
                event=REGISTRATION_VERIFICATION,
            )
            row.delivery_code = ""
            await session.commit()

        await task_progress(stage="registration", message="Sending registration verification email")
        status, error, duration_ms = await send_security_mail(mail, recipient, identity, message)
        async with database.session_factory() as session:
            updated = await session.scalar(
                update(PendingRegistration)
                .where(PendingRegistration.id == identity, PendingRegistration.status == "sending")
                .values(
                    status=status,
                    lease_until=None,
                    sent_at=datetime.now(UTC) if status == "sent" else None,
                )
                .returning(PendingRegistration.id)
            )
            await session.commit()
        if updated is not None:
            dispatch(
                handlers,
                delivery_event(
                    status,
                    EMAIL,
                    identity,
                    kind=REGISTRATION_VERIFICATION,
                    reason=error,
                    stage="security_mail",
                    recipient=recipient,
                    mail_profile_id=mail.id,
                    duration_ms=duration_ms,
                    attempt=1,
                ),
            )
        return {"processed": 1}
    finally:
        await database.close()
