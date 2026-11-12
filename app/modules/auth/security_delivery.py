"""Render and send security mail without recording codes or recipient data in task logs."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.notifications.catalog import EMAIL_VERIFICATION
from app.modules.notifications.render import (
    header_value,
    render_html_template,
    render_template,
    sanitize_email_html,
)
from app.modules.notifications.service import NotificationService
from app.modules.settings.mail_service import MailProfile
from app.modules.settings.mail_transport import send_notification


async def verification_message(
    session: AsyncSession, config: Settings, locale: str, code: str, minutes: int = 15
) -> tuple[str, str, str]:
    _, subject, text, html, html_enabled, _ = await NotificationService(
        session, config
    )._template_for(EMAIL_VERIFICATION, "email", locale)
    variables = {"verification": {"code": code, "expires_minutes": minutes}}
    return (
        header_value(render_template(subject, variables)),
        render_template(text, variables),
        sanitize_email_html(render_html_template(html, variables)) if html_enabled else "",
    )


async def send_security_mail(
    profile: MailProfile, recipient: str, identity: UUID, message: tuple[str, str, str]
) -> tuple[str, str | None]:
    budget = min(120, max(15, profile.value.timeout_seconds * 4)) + 5
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                send_notification, profile.value, profile.password, recipient, identity, *message
            ),
            timeout=budget,
        )
    except TimeoutError:
        return "unknown", "delivery_unknown"
