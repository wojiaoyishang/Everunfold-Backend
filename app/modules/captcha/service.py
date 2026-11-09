"""One-time challenges, policy fencing and private configuration. Redis is never the policy fact."""

import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

from anyio import to_thread
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_keys import redis_key
from app.core.runtime import Runtime
from app.modules.auth.service import AuthFailure
from app.modules.captcha.image import generate_image
from app.modules.captcha.providers import provider_catalog
from app.modules.captcha.schemas import (
    CaptchaAnswer,
    CaptchaChallenge,
    CaptchaConfig,
    CaptchaSettingsState,
    ChallengeRequest,
    ProviderDescription,
    PublicProviderSettings,
    SaveCaptchaSettings,
    StoredProvider,
)
from app.modules.content.models import Content, SiteSettings
from app.modules.content.repository import presentation_conditions
from app.modules.pages.models import ManagedPage

CONFIG_KEY = "_captcha_private"
TTL = 180


def config_from_data(data: dict[str, object]) -> CaptchaConfig:
    return CaptchaConfig.model_validate(data.get(CONFIG_KEY, {}))


def required(config: CaptchaConfig, action: str, kind: str, value: dict[str, object]) -> bool:
    override = value.get(f"captcha_{action}", "inherit")
    if override != "inherit":
        return override == "required"
    if action == "login":
        return config.rules.login
    rules = config.rules.read if action == "read" else config.rules.comment
    return bool(getattr(rules, kind, False))


async def current_config(session: AsyncSession) -> CaptchaConfig:
    site = await session.get(SiteSettings, 1)
    return config_from_data(site.data) if site else CaptchaConfig()


@dataclass(frozen=True)
class CaptchaContext:
    config: CaptchaConfig
    required: bool
    stamp: str
    action: str
    content_id: UUID | None


async def context(session: AsyncSession, action: str, content_id: UUID | None) -> CaptchaContext:
    config = await current_config(session)
    generation = 0
    policy: dict[str, object] = {}
    kind = ""
    if content_id is not None:
        item = await session.scalar(
            select(Content).where(Content.id == content_id, *presentation_conditions())
        )
        if item:
            kind, policy, generation = item.kind, item.editor_settings or {}, item.public_generation
        else:
            page = await session.get(ManagedPage, content_id)
            if page is None or page.deleted_at is not None or page.published is None:
                raise AuthFailure(404, 40486, "内容不存在")
            kind, policy, generation = (
                "page",
                page.published,
                page.applied_revision or page.revision,
            )
    needed = required(config, action, kind, policy)
    stamp = hashlib.sha256(
        f"{config.revision}:{action}:{content_id}:{generation}:{needed}".encode()
    ).hexdigest()
    return CaptchaContext(config, needed, stamp, action, content_id)


class CaptchaService:
    def __init__(self, runtime: Runtime, peer: str) -> None:
        self.runtime, self.peer = runtime, peer

    async def _limit(self, action: str, limit: int) -> None:
        key = redis_key(
            self.runtime.settings,
            "captcha",
            "limit",
            action,
            hashlib.sha256(self.peer.encode()).hexdigest(),
        )
        command = self.runtime.redis.eval(
            "local n=redis.call('INCR',KEYS[1]); "
            "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
            1,
            key,
        )
        count = await cast(Awaitable[int], command)
        if int(count) > limit:
            raise AuthFailure(429, 42986, "验证码请求较多，请稍后重试", 60)

    async def policy(self, action: str, content_id: UUID | None) -> CaptchaContext:
        async with self.runtime.database.session_factory() as session:
            return await context(session, action, content_id)

    def _key(self, challenge_id: UUID) -> str:
        return redis_key(self.runtime.settings, "captcha", "challenge", str(challenge_id))

    async def challenge(self, payload: ChallengeRequest) -> CaptchaChallenge:
        try:
            await self._limit("challenge", 40)
            policy = await self.policy(payload.action, payload.content_id)
            if not policy.required:
                return CaptchaChallenge(required=False)
            provider = policy.config.provider
            settings = policy.config.providers.get(provider, StoredProvider())
            challenge_id = uuid4()
            stored = {
                "stamp": policy.stamp,
                "client": str(payload.client_id),
                "peer": hashlib.sha256(self.peer.encode()).hexdigest(),
            }
            image, widget, salt, digest = "", "", "", ""
            if provider == "local":
                answer, image = await to_thread.run_sync(generate_image)
                salt = secrets.token_hex(24)
                digest = hashlib.sha256((salt + answer).encode()).hexdigest()
            else:
                definition = provider_catalog().get(provider)
                if (
                    definition is None
                    or not settings.secret
                    or not settings.site_key
                    or not settings.hostnames
                ):
                    raise AuthFailure(503, 50386, "验证码提供方尚未配置完整，请联系管理员")
                widget = definition.widget
            stored.update(salt=salt, digest=digest)
            await self.runtime.redis.set(self._key(challenge_id), json.dumps(stored), ex=TTL)
            return CaptchaChallenge(
                required=True,
                provider=provider,
                widget=widget,
                site_key=settings.site_key,
                challenge_id=challenge_id,
                expires_in=TTL,
                image=image,
            )
        except RedisError:
            raise AuthFailure(503, 50386, "验证码服务暂不可用，请稍后重试") from None

    async def enforce(
        self, action: str, content_id: UUID | None, answer: CaptchaAnswer | None
    ) -> CaptchaContext:
        policy = await self.policy(action, content_id)
        if not policy.required:
            return policy
        if answer is None:
            raise AuthFailure(403, 40386, "请先完成验证码")
        try:
            await self._limit("verify", 60)
            raw = await self.runtime.redis.getdel(self._key(answer.challenge_id))
        except RedisError:
            raise AuthFailure(503, 50386, "验证码服务暂不可用，请稍后重试") from None
        if not raw:
            raise AuthFailure(403, 40387, "验证码已过期或已使用，请刷新")
        stored = json.loads(raw)
        if (
            stored.get("stamp") != policy.stamp
            or stored.get("client") != str(answer.client_id)
            or stored.get("peer") != hashlib.sha256(self.peer.encode()).hexdigest()
        ):
            raise AuthFailure(403, 40387, "验证码已失效，请刷新")
        if policy.config.provider == "local":
            actual = hashlib.sha256(
                (stored["salt"] + answer.answer.strip().upper()).encode()
            ).hexdigest()
            valid = hmac.compare_digest(actual, stored["digest"])
        else:
            provider = provider_catalog().get(policy.config.provider)
            if provider is None:
                raise AuthFailure(503, 50386, "验证码提供方不可用")
            valid = await provider.verify(answer.answer, policy.config.providers[provider.id])
        if not valid:
            raise AuthFailure(403, 40387, "验证码不正确，请刷新后重试")
        return policy


async def guard(session: AsyncSession, permit: CaptchaContext) -> None:
    # Site-before-content order matches publication. External HTTP has already finished.
    await session.scalar(
        select(SiteSettings).where(SiteSettings.id == 1).with_for_update(read=True)
    )
    current = await context(session, permit.action, permit.content_id)
    if current.stamp != permit.stamp:
        raise AuthFailure(409, 40986, "验证期间内容或规则已更新，请刷新验证码")


def settings_state(config: CaptchaConfig) -> CaptchaSettingsState:
    return CaptchaSettingsState(
        revision=config.revision,
        provider=config.provider,
        rules=config.rules,
        providers={
            key: PublicProviderSettings(
                site_key=value.site_key,
                hostnames=value.hostnames,
                secret_configured=bool(value.secret),
            )
            for key, value in config.providers.items()
        },
        catalog=[
            ProviderDescription(id="local", title="本地图片验证码", widget=""),
            *[item.description() for item in provider_catalog().values()],
        ],
    )


async def save_settings(
    session: AsyncSession, runtime: Runtime, payload: SaveCaptchaSettings, actor_id: UUID
) -> CaptchaSettingsState:
    from app.modules.content.publication import ContentPublication
    from app.modules.media.locking import lock_references
    from app.modules.settings.models import SettingsAudit
    from app.modules.settings.service import SettingsService

    await lock_references(session)
    site = await SettingsService(session, runtime.settings).site(locked=True)
    before = config_from_data(site.data)
    if payload.expected_revision != before.revision:
        raise AuthFailure(409, 40986, "验证码设置已更新，请重新读取后再保存")
    if payload.provider != "local" and payload.provider not in provider_catalog():
        raise AuthFailure(422, 42286, "验证码提供方未注册")
    providers = dict(before.providers)
    for key, value in payload.providers.items():
        if key not in provider_catalog():
            raise AuthFailure(422, 42286, "验证码提供方未注册")
        secret = (
            ""
            if value.clear_secret
            else value.secret or providers.get(key, StoredProvider()).secret
        )
        providers[key] = StoredProvider(
            site_key=value.site_key.strip(), hostnames=value.hostnames, secret=secret
        )
    if payload.provider != "local":
        selected = providers.get(payload.provider)
        if not selected or not selected.secret or not selected.site_key or not selected.hostnames:
            raise AuthFailure(422, 42286, "请填写 Site Key、Secret 和允许的主机名")
    updated = CaptchaConfig(
        revision=before.revision + 1,
        provider=payload.provider,
        providers=providers,
        rules=payload.rules,
    )
    site.data = {**site.data, CONFIG_KEY: updated.model_dump(mode="json")}
    for item in await session.scalars(select(Content)):
        item.captcha_read_required = required(
            updated, "read", item.kind, item.editor_settings or {}
        )
    session.add(
        SettingsAudit(
            user_id=actor_id,
            resource="captcha",
            revision=updated.revision,
            changes={"action": "configure", "provider": updated.provider},
        )
    )
    await session.flush()
    await ContentPublication(session, runtime.settings).stage()
    await session.commit()
    return settings_state(updated)
