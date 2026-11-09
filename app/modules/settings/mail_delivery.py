"""Scheduler dispatches this drain to Worker. Each explicit test is attempted at most once."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_schemas import MailPreferences
from app.modules.settings.mail_service import MAIL_KEY
from app.modules.settings.mail_test_service import mail_ready
from app.modules.settings.mail_transport import send_test
from app.modules.settings.models import MailTestDelivery


async def deliver_test(config: Settings) -> dict[str, int]:
    db = Database(config.database.url.get_secret_value())
    try:
        now = datetime.now(UTC)
        async with db.session_factory() as session:
            await session.execute(
                delete(MailTestDelivery).where(
                    MailTestDelivery.finished_at < now - timedelta(days=30),
                )
            )
            await session.execute(
                update(MailTestDelivery)
                .where(
                    MailTestDelivery.status == "sending",
                    MailTestDelivery.lease_until < now,
                )
                .values(status="unknown", error="interrupted", finished_at=now)
            )
            await session.execute(
                update(MailTestDelivery)
                .where(
                    MailTestDelivery.status == "queued",
                    MailTestDelivery.created_at < now - timedelta(minutes=10),
                )
                .values(status="failed", error="queue_timeout", finished_at=now)
            )
            row = await session.scalar(
                select(MailTestDelivery)
                .where(
                    MailTestDelivery.status == "queued",
                )
                .order_by(MailTestDelivery.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not row:
                await session.commit()
                return {"processed": 0}
            site = await session.get(SiteSettings, 1)
            stored = site.data.get(MAIL_KEY, {}) if site else {}
            value = MailPreferences.model_validate(stored.get("value", {}))
            password = stored.get("password", "")
            if stored.get("revision", 0) != row.revision or not mail_ready(value, password):
                row.status, row.error, row.finished_at = "failed", "configuration_changed", now
                await session.commit()
                return {"processed": 1}
            budget = min(120, max(15, value.timeout_seconds * 4)) + 5
            row.status, row.lease_until = "sending", now + timedelta(seconds=budget + 15)
            identity, recipient = row.id, row.recipient
            await session.commit()
        try:
            status, error = await asyncio.wait_for(
                asyncio.to_thread(send_test, value, password, recipient, identity),
                timeout=budget,
            )
        except TimeoutError:
            status, error = "unknown", "delivery_unknown"
        async with db.session_factory() as session:
            await session.execute(
                update(MailTestDelivery)
                .where(
                    MailTestDelivery.id == identity,
                    MailTestDelivery.status == "sending",
                )
                .values(status=status, error=error, finished_at=datetime.now(UTC))
            )
            await session.commit()
        return {"processed": 1}
    finally:
        await db.close()
