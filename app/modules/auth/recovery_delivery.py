"""Claim and send password recovery mail outside the database transaction."""

from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import case, delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.progress import task_progress
from app.modules.auth.models import User
from app.modules.auth.recovery_models import PasswordRecovery
from app.modules.auth.recovery_service import recovery_user
from app.modules.auth.security_delivery import security_message, send_security_mail
from app.modules.auth.security_settings import recovery_policy
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import PASSWORD_RECOVERY, PASSWORD_RESET_COMPLETED
from app.modules.notifications.routing import auth_mail_route
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles


async def deliver(config: Settings) -> dict[str, int]:
    database = Database(config.database.url.get_secret_value())
    try:
        now = datetime.now(UTC)
        async with database.session_factory() as session:
            await session.execute(
                delete(PasswordRecovery).where(PasswordRecovery.expires_at <= now)
            )
            await session.execute(
                update(PasswordRecovery)
                .where(
                    PasswordRecovery.status.in_(("sending", "notice_sending")),
                    PasswordRecovery.lease_until <= now,
                )
                .values(
                    status=case((PasswordRecovery.status == "sending", "unknown"), else_="done"),
                    delivery_code="",
                    lease_until=None,
                )
            )
            row = await session.scalar(
                select(PasswordRecovery)
                .where(
                    PasswordRecovery.status.in_(("queued", "notice_queued")),
                    PasswordRecovery.expires_at > now,
                )
                .order_by(PasswordRecovery.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not row:
                await session.commit()
                return {"processed": 0}
            notice = row.status == "notice_queued"
            site = await session.get(SiteSettings, 1)
            data = site.data if site else {}
            mail = mail_profile(data, row.mail_profile_id)
            user = await session.get(User, row.user_id) if row.user_id else None
            enabled, epoch = recovery_policy(data)
            notice_disabled = (
                notice and not (await auth_mail_route(session, PASSWORD_RESET_COMPLETED)).enabled
            )
            if (
                not user
                or not recovery_user(row, user)
                or not mail
                or not mail.enabled
                or not mail_ready(mail.value, mail.password)
                or stored_mail_profiles(data)[0] != row.mail_revision
                or notice_disabled
                or (
                    not notice
                    and (not enabled or epoch != row.policy_epoch or not row.delivery_code)
                )
            ):
                row.status = "done" if notice else "failed"
                row.delivery_code = ""
                await session.commit()
                return {"processed": 1}
            row.status = "notice_sending" if notice else "sending"
            row.lease_until = now + timedelta(seconds=150)
            identity, recipient = row.id, row.email
            message = await security_message(
                session,
                config,
                PASSWORD_RESET_COMPLETED if notice else PASSWORD_RECOVERY,
                user.preferred_locale,
                display_name=user.display_name,
                verification=None
                if notice
                else (row.delivery_code, max(1, ceil((row.expires_at - now).total_seconds() / 60))),
            )
            row.delivery_code = ""
            await session.commit()

        await task_progress(stage="password-recovery", message="Sending account security email")
        message_id = (
            uuid5(NAMESPACE_URL, f"password-reset-notice:{identity}") if notice else identity
        )
        result, _ = await send_security_mail(mail, recipient, message_id, message)
        async with database.session_factory() as session:
            await session.execute(
                update(PasswordRecovery)
                .where(
                    PasswordRecovery.id == identity,
                    PasswordRecovery.status == ("notice_sending" if notice else "sending"),
                )
                .values(status="done" if notice else result, lease_until=None)
            )
            await session.commit()
        return {"processed": 1}
    finally:
        await database.close()
