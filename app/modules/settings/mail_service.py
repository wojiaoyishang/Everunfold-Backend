"""Private mail profiles with one default sender and write-only credentials."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.auth.service import AuthFailure
from app.modules.settings.mail_schemas import (
    MailPreferences,
    MailSenderProfileView,
    MailSettingsState,
    SaveMailSettings,
)
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService

MAIL_KEY = "_mail_private"
LEGACY_PROFILE_ID = UUID("00000000-0000-4000-8000-000000000501")


@dataclass(frozen=True)
class MailProfile:
    id: UUID
    name: str
    enabled: bool
    is_default: bool
    value: MailPreferences
    password: str


def mail_ready(value: MailPreferences, password: str) -> bool:
    return bool(
        value.enabled
        and value.host
        and value.from_address
        and (not value.authenticate or (value.username and password))
    )


def stored_mail_profiles(data: dict[str, Any]) -> tuple[int, list[MailProfile]]:
    stored = data.get(MAIL_KEY, {})
    revision = int(stored.get("revision", 0))
    rows = stored.get("profiles")
    if not isinstance(rows, list):
        # The first release stored one SMTP profile directly under value/password.
        legacy = stored.get("value")
        if not isinstance(legacy, dict):
            return revision, []
        rows = [
            {
                "id": str(LEGACY_PROFILE_ID),
                "name": "默认邮件配置",
                "enabled": bool(legacy.get("enabled")),
                "is_default": True,
                "value": legacy,
                "password": stored.get("password", ""),
            }
        ]
    profiles: list[MailProfile] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            profile_id = UUID(str(row["id"]))
        except (KeyError, ValueError):
            continue
        profiles.append(
            MailProfile(
                id=profile_id,
                name=str(row.get("name", "邮件配置")).strip() or "邮件配置",
                enabled=bool(row.get("enabled")),
                is_default=bool(row.get("is_default")),
                value=MailPreferences.model_validate(row.get("value", {})),
                password=str(row.get("password", "")),
            )
        )
    return revision, profiles


def default_mail_profile(data: dict[str, Any]) -> MailProfile | None:
    _, profiles = stored_mail_profiles(data)
    return next((item for item in profiles if item.is_default), None)


def mail_profile(data: dict[str, Any], profile_id: UUID | str | None) -> MailProfile | None:
    _, profiles = stored_mail_profiles(data)
    if profile_id is None:
        return next((item for item in profiles if item.is_default), None)
    try:
        selected = UUID(str(profile_id))
    except ValueError:
        return None
    return next((item for item in profiles if item.id == selected), None)


def mail_profile_view(profile: MailProfile) -> MailSenderProfileView:
    return MailSenderProfileView(
        id=profile.id,
        name=profile.name,
        enabled=profile.enabled,
        is_default=profile.is_default,
        value=profile.value,
        password_configured=bool(profile.password),
    )


def mail_settings_state(data: dict[str, Any]) -> MailSettingsState:
    revision, profiles = stored_mail_profiles(data)
    return MailSettingsState(
        revision=revision,
        profiles=[mail_profile_view(profile) for profile in profiles],
    )


class MailSettingsService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings

    async def state(self) -> MailSettingsState:
        site = await self.settings.site()
        return mail_settings_state(site.data)

    async def save(self, payload: SaveMailSettings, actor_id: UUID) -> MailSettingsState:
        site = await self.settings.site(locked=True)
        revision, before = stored_mail_profiles(site.data)
        if payload.expected_revision != revision:
            raise AuthFailure(409, 40999, "邮件配置已在其他地方修改，请重新读取后合并")
        old = {profile.id: profile for profile in before}
        removed_ids = set(old) - {profile.id for profile in payload.profiles}
        if removed_ids:
            from app.modules.notifications.models import NotificationRoute

            used = set(
                await self.settings.session.scalars(
                    select(NotificationRoute.mail_profile_id).where(
                        NotificationRoute.mail_profile_id.in_(
                            [str(profile_id) for profile_id in removed_ids]
                        )
                    )
                )
            )
            if used:
                raise AuthFailure(
                    422,
                    42299,
                    "发信配置仍被提醒规则使用，请先改绑到其他配置",
                )
        rows: list[dict[str, object]] = []
        for item in payload.profiles:
            previous = old.get(item.id)
            password = (
                ""
                if item.clear_password
                else (
                    item.password.get_secret_value()
                    if item.password and item.password.get_secret_value()
                    else (previous.password if previous else "")
                )
            )
            if item.enabled and not mail_ready(item.value, password):
                raise AuthFailure(
                    422,
                    42299,
                    "启用发信配置前请填写服务器、发件地址及所需的认证信息",
                )
            rows.append(
                {
                    "id": str(item.id),
                    "name": item.name,
                    "enabled": item.enabled,
                    "is_default": item.is_default,
                    "value": item.value.model_dump(mode="json"),
                    "password": password,
                }
            )
        next_revision = revision + 1
        site.data = {
            **site.data,
            MAIL_KEY: {"revision": next_revision, "profiles": rows},
        }
        self.settings.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="mail",
                revision=next_revision,
                changes={
                    "action": "replace_profiles",
                    "profiles": [
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "enabled": row["enabled"],
                            "is_default": row["is_default"],
                            "password_changed": (
                                row["password"] != old[UUID(str(row["id"]))].password
                                if UUID(str(row["id"])) in old
                                else row["password"] != ""
                            ),
                        }
                        for row in rows
                    ],
                },
            )
        )
        await self.settings.session.commit()
        return mail_settings_state(site.data)
