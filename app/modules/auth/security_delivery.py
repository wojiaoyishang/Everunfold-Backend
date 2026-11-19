"""Render and send security mail without recording codes or recipient data in task logs."""

import asyncio
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import EMAIL_VERIFICATION, VERIFICATION_PURPOSES
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


async def security_message(
    session: AsyncSession,
    config: Settings,
    event: str,
    locale: str,
    *,
    display_name: str,
    verification: tuple[str, int] | None = None,
) -> tuple[str, str, str]:
    template, subject, text, html, html_enabled, _ = await NotificationService(
        session, config
    )._template_for(event, "email", locale)
    language = template.locale if template else locale
    site = await session.get(SiteSettings, 1)
    data = site.data if site else {}
    variables: dict[str, Any] = {
        "site": await site_variables(session, config, data, stored_brand(data)),
        "recipient": {"display_name": display_name},
        "mail": {"reason": mail_reason(event, language)},
    }
    if verification is not None:
        code, minutes = verification
        variables["verification"] = {
            "code": code,
            "expires_minutes": minutes,
            "purpose": VERIFICATION_PURPOSES[event][english(language)],
        }
    return (
        header_value(render_template(subject, variables)),
        render_template(text, variables),
        sanitize_email_html(render_html_template(html, variables)) if html_enabled else "",
    )


async def verification_message(
    session: AsyncSession,
    config: Settings,
    locale: str,
    code: str,
    minutes: int,
    *,
    display_name: str,
    event: str = EMAIL_VERIFICATION,
) -> tuple[str, str, str]:
    return await security_message(
        session, config, event, locale, display_name=display_name, verification=(code, minutes)
    )


async def send_security_mail(
    profile: MailProfile, recipient: str, identity: UUID, message: tuple[str, str, str]
) -> tuple[str, str | None, float]:
    budget = min(120, max(15, profile.value.timeout_seconds * 4)) + 5
    started = perf_counter()
    try:
        status, error = await asyncio.wait_for(
            asyncio.to_thread(
                send_notification, profile.value, profile.password, recipient, identity, *message
            ),
            timeout=budget,
        )
    except TimeoutError:
        status, error = "unknown", "delivery_unknown"
    return status, error, round((perf_counter() - started) * 1000, 3)
