"""Render and send security mail without recording codes or recipient data in task logs."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import EMAIL_VERIFICATION
from app.modules.notifications.email_context import (
    english,
    mail_reason,
    site_variables,
    stored_brand,
)
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
    session: AsyncSession,
    config: Settings,
    locale: str,
    code: str,
    minutes: int,
    *,
    display_name: str,
    registration: bool = False,
) -> tuple[str, str, str]:
    template, subject, text, html, html_enabled, _ = await NotificationService(
        session, config
    )._template_for(EMAIL_VERIFICATION, "email", locale)
    language = template.locale if template else locale
    purpose = (
        ("注册账号", "Account registration") if registration else ("验证邮箱", "Email verification")
    )
    site = await session.get(SiteSettings, 1)
    data = site.data if site else {}
    variables = {
        "verification": {
            "code": code,
            "expires_minutes": minutes,
            "purpose": purpose[english(language)],
        },
        "site": await site_variables(session, config, data, stored_brand(data)),
        "recipient": {"display_name": display_name},
        "mail": {"reason": mail_reason(EMAIL_VERIFICATION, language)},
    }
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
