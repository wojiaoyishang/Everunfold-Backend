"""Mail configuration only; saving never opens a connection or queues a message."""

from uuid import UUID

from app.modules.auth.service import AuthFailure
from app.modules.settings.mail_schemas import MailPreferences, MailSettingsState, SaveMailSettings
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService

MAIL_KEY = "_mail_private"


class MailSettingsService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings

    async def state(self) -> MailSettingsState:
        site = await self.settings.site()
        stored = site.data.get(MAIL_KEY, {})
        return MailSettingsState(
            revision=stored.get("revision", 0),
            value=MailPreferences.model_validate(stored.get("value", {})),
            password_configured=bool(stored.get("password")),
        )

    async def save(self, payload: SaveMailSettings, actor_id: UUID) -> MailSettingsState:
        site = await self.settings.site(locked=True)
        before = site.data.get(MAIL_KEY, {})
        revision = before.get("revision", 0)
        if payload.expected_revision != revision:
            raise AuthFailure(409, 40999, "邮件配置已在其他地方修改，请重新读取后合并")
        password = (
            ""
            if payload.clear_password
            else (
                payload.password.get_secret_value()
                if payload.password and payload.password.get_secret_value()
                else before.get("password", "")
            )
        )
        value = payload.value
        if value.enabled and (
            not value.host
            or not value.from_address
            or (value.authenticate and (not value.username or not password))
        ):
            raise AuthFailure(422, 42299, "启用前请填写SMTP服务器、发件地址及所需的认证信息")
        site.data = {
            **site.data,
            MAIL_KEY: {
                "revision": revision + 1,
                "value": value.model_dump(mode="json"),
                "password": password,
            },
        }
        self.settings.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="mail",
                revision=revision + 1,
                changes={
                    "action": "configure",
                    "enabled": value.enabled,
                    "password_changed": password != before.get("password", ""),
                },
            )
        )
        await self.settings.session.commit()
        return MailSettingsState(
            revision=revision + 1, value=value, password_configured=bool(password)
        )
