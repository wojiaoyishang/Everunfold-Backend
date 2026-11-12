"""Private access policy. SMTP credentials and CAPTCHA providers remain in their modules."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime import Runtime
from app.modules.auth.accounts_schemas import (
    AccessSenderOption,
    AccessSettingsInput,
    AccessSettingsState,
)
from app.modules.auth.registration_schemas import RegistrationMode
from app.modules.auth.service import AuthFailure
from app.modules.captcha.service import CONFIG_KEY, config_from_data
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService

ACCESS_KEY = "_access_private"


def registration_mode(data: dict[str, Any]) -> RegistrationMode:
    value = data.get(ACCESS_KEY, {})
    mode = value.get("registration_mode") if isinstance(value, dict) else None
    if mode == "invite":
        return "invite"
    if mode == "open":
        return "open"
    return "closed"


def configured_sender(data: dict[str, Any]) -> str | None:
    config = data.get(ACCESS_KEY)
    if not isinstance(config, dict):
        return None
    sender = config.get("mail_profile_id")
    return sender if isinstance(sender, str) else None


def access_state(data: dict[str, Any]) -> AccessSettingsState:
    captcha = config_from_data(data)
    sender = configured_sender(data)
    selected = mail_profile(data, sender)
    _, profiles = stored_mail_profiles(data)
    return AccessSettingsState(
        revision=captcha.revision,
        login_captcha=captcha.rules.login,
        registration_mode=registration_mode(data),
        registration_captcha=captcha.rules.registration,
        email_verification_captcha=captcha.rules.email_verification,
        mail_profile_id=UUID(sender) if sender else None,
        mail_ready=bool(
            selected and selected.enabled and mail_ready(selected.value, selected.password)
        ),
        mail_profiles=[
            AccessSenderOption(id=item.id, name=item.name)
            for item in profiles
            if item.enabled and mail_ready(item.value, item.password)
        ],
    )


async def read_access_settings(session: AsyncSession) -> AccessSettingsState:
    site = await session.scalar(select(SiteSettings).where(SiteSettings.id == 1))
    return access_state(site.data if site else {})


async def save_access_settings(
    session: AsyncSession, runtime: Runtime, actor_id: UUID, payload: AccessSettingsInput
) -> AccessSettingsState:
    site = await SettingsService(session, runtime.settings).site(locked=True)
    captcha = config_from_data(site.data)
    if payload.expected_revision != captcha.revision:
        raise AuthFailure(409, 40986, "验证码或认证设置已更新，请重新读取后保存")
    sender = payload.mail_profile_id
    if sender is not None:
        selected = mail_profile(site.data, sender)
        if (
            not selected
            or not selected.enabled
            or not mail_ready(selected.value, selected.password)
        ):
            raise AuthFailure(422, 42272, "请选择已启用且配置完整的发信配置")
    captcha.revision += 1
    captcha.rules.login = payload.login_captcha
    if payload.registration_captcha is not None:
        captcha.rules.registration = payload.registration_captcha
    captcha.rules.email_verification = payload.email_verification_captcha
    site.data = {
        **site.data,
        CONFIG_KEY: captcha.model_dump(mode="json"),
        ACCESS_KEY: {
            **site.data.get(ACCESS_KEY, {}),
            "mail_profile_id": str(sender) if sender else None,
            "registration_mode": payload.registration_mode or registration_mode(site.data),
        },
    }
    session.add(
        SettingsAudit(
            user_id=actor_id,
            resource="access",
            revision=captcha.revision,
            changes=payload.model_dump(mode="json", exclude={"expected_revision"}),
        )
    )
    await session.commit()
    return access_state(site.data)
