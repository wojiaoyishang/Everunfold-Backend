"""Authoring admission and optimistic configuration updates owned by this plugin."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.permissions import Actor
from app.modules.auth.resource_access import require_content_access
from app.modules.auth.service import AuthFailure
from app.modules.captcha.service import current_config, required
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content
from app.modules.pages.models import ManagedPage
from app.modules.pages.types import registered_types
from app.modules.settings.models import SettingsAudit
from app.plugins.glossary.models import GlossaryTerm

from .models import AiWritingRequest, AiWritingSettings, StoredProvider
from .safety import cipher, reviewed_text
from .schemas import (
    PERMISSIONS,
    TARGETS,
    AiAvailability,
    AiConfiguration,
    AiGeneration,
    AiProviderConfiguration,
    AiProviderSummary,
    GenerateAiText,
    SaveAiConfiguration,
    SaveAiProvider,
    Scene,
    Status,
    TestAiConnection,
)
from .transport import endpoint, provider_origin

QUEUED_TTL = timedelta(minutes=3)
RESULT_TTL = timedelta(minutes=30)


def now() -> datetime:
    return datetime.now(UTC)


async def configuration(session: AsyncSession, *, locked: bool = False) -> AiWritingSettings:
    if locked:
        row = await session.scalar(
            select(AiWritingSettings)
            .where(AiWritingSettings.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        row = await session.get(AiWritingSettings, 1)
    if row is None:
        raise AuthFailure(503, 50389, "AI 写作助手配置暂不可用")
    return row


def configuration_view(row: AiWritingSettings) -> AiConfiguration:
    return AiConfiguration(
        revision=row.revision,
        enabled=row.enabled,
        fields=row.fields,
        default_provider_id=row.default_provider_id,
        providers=[
            AiProviderConfiguration(
                id=UUID(provider["id"]),
                name=provider["name"],
                base_url=provider["base_url"],
                model=provider["model"],
                api_key_configured=bool(provider["key_ciphertext"]),
            )
            for provider in row.providers
        ],
    )


def store_provider(
    config: Settings, payload: SaveAiProvider, previous: StoredProvider | None
) -> StoredProvider:
    base_url = payload.base_url.rstrip("/")
    try:
        next_url = endpoint(base_url)
        prior_url = endpoint(previous["base_url"]) if previous and previous["base_url"] else None
    except ValueError:
        raise AuthFailure(422, 42289, "模型接口地址必须是有效的 HTTP(S) 地址") from None
    key = previous["key_ciphertext"] if previous else None
    if payload.clear_api_key:
        key = None
    elif payload.api_key is not None:
        key = cipher(config, "key").encrypt(payload.api_key.get_secret_value().encode()).decode()
    elif key and (prior_url is None or provider_origin(next_url) != provider_origin(prior_url)):
        raise AuthFailure(422, 42289, "更换接口来源时请替换或清除原 API Key")
    if key and next_url.scheme != "https":
        raise AuthFailure(422, 42289, "使用 API Key 的模型接口须通过 HTTPS 连接")
    return {
        "id": str(payload.id),
        "name": payload.name,
        "base_url": base_url,
        "model": payload.model,
        "key_ciphertext": key,
    }


async def save_configuration(
    session: AsyncSession, config: Settings, actor: Actor, payload: SaveAiConfiguration
) -> AiConfiguration:
    row = await configuration(session, locked=True)
    if row.revision != payload.revision:
        raise AuthFailure(409, 40989, "配置已修改，请重新读取后再保存")
    prior = {item["id"]: item for item in row.providers}
    providers = [
        store_provider(config, item, prior.get(str(item.id))) for item in payload.providers
    ]
    row.enabled, row.fields = payload.enabled, dict(payload.fields)
    row.default_provider_id, row.providers = payload.default_provider_id, providers
    row.revision += 1
    session.add(
        SettingsAudit(
            user_id=actor.id,
            resource="ai-writing",
            revision=row.revision,
            changes={
                "action": "configuration_changed",
                "enabled": row.enabled,
                "default_provider_id": str(row.default_provider_id)
                if row.default_provider_id
                else None,
                "providers": [
                    {
                        "id": provider["id"],
                        "hostname": urlsplit(provider["base_url"]).hostname or "",
                        "key_configured": bool(provider["key_ciphertext"]),
                    }
                    for provider in providers
                ],
                "fields": row.fields,
            },
        )
    )
    return configuration_view(row)


def configured_provider(row: AiWritingSettings, identity: UUID | None) -> StoredProvider | None:
    return next(
        (
            provider
            for provider in row.providers
            if provider["id"] == str(identity) and provider["base_url"] and provider["model"]
        ),
        None,
    )


def allowed_scene(actor: Actor, scene: Scene) -> bool:
    permission = PERMISSIONS[scene]
    if scene == "glossary.edit":
        # The glossary mutations require the all-content editing capability.
        return actor.allows(permission)
    return actor.has(permission)


async def availability(session: AsyncSession, actor: Actor) -> AiAvailability:
    if not any(allowed_scene(actor, scene) for scene in PERMISSIONS):
        raise AuthFailure(403, 40389, "没有使用 AI 写作助手的权限")
    row = await configuration(session)
    captcha = await current_config(session)
    providers = [
        AiProviderSummary(id=UUID(provider["id"]), name=provider["name"], model=provider["model"])
        for provider in row.providers
        if row.enabled and provider["base_url"] and provider["model"]
    ]
    return AiAvailability(
        revision=row.revision,
        enabled=bool(providers),
        fields={
            scene: row.fields[scene]
            for scene in PERMISSIONS
            if allowed_scene(actor, scene) and scene in row.fields
        },
        default_provider_id=row.default_provider_id if providers else None,
        providers=providers,
        restricted_by_default={
            kind: required(captcha, "read", kind, {}) for kind in ("article", "shuoshuo", "page")
        },
    )


async def target_access(
    session: AsyncSession,
    config: Settings,
    actor: Actor,
    scene: Scene,
    field: str,
    resource_id: UUID | None,
) -> bool:
    """Check resource permissions and return the effective body/summary restriction."""
    if field not in TARGETS[scene] or not allowed_scene(actor, scene):
        raise AuthFailure(403, 40389, "没有使用 AI 写作助手的权限")
    if scene == "settings.site":
        if resource_id is not None:
            raise AuthFailure(422, 42289, "站点设置无需内容编号")
        return False
    if scene == "glossary.edit":
        if resource_id is not None and await session.get(GlossaryTerm, resource_id) is None:
            raise AuthFailure(404, 40489, "编辑内容不存在")
        return False
    captcha = await current_config(session)
    if scene == "pages.edit":
        page = await session.get(ManagedPage, resource_id) if resource_id else None
        if resource_id and (page is None or page.deleted_at is not None):
            raise AuthFailure(404, 40489, "编辑内容不存在")
        if page and page.implementation_id and field.startswith("seo_"):
            registration = (await registered_types(session, config, page.implementation_id)).get(
                page.implementation_id
            )
            if (
                not registration
                or not registration.available
                or not registration.definition.seo_editable
            ):
                raise AuthFailure(403, 40389, "此页面不允许编辑 SEO")
        policies = [page.data, page.applied_snapshot] if page else [{}]
        return any(
            required(captcha, "read", "page", policy) for policy in policies if policy is not None
        )

    kind = "article" if scene == "content.article.edit" else "shuoshuo"
    if resource_id is None:
        return required(captcha, "read", kind, {})
    await require_content_access(session, actor, "content:write", resource_id)
    draft = await session.get(ContentDraft, resource_id)
    if draft is None or draft.deleted_at is not None or draft.type_id != kind:
        raise AuthFailure(404, 40489, "编辑内容不存在")
    values = draft.editor_settings or {}
    published = await session.get(Content, resource_id) if draft.published_revision else None
    publication_restricted = bool(
        published
        and (
            published.visibility != "public"
            or published.captcha_read_required
            or (published.opens_at and published.opens_at > now())
            or required(captcha, "read", kind, published.editor_settings or {})
        )
    )
    return bool(
        draft.access_password_hash
        or values.get("visibility") == "password"
        or values.get("opens_at")
        or publication_restricted
        or required(captcha, "read", kind, values)
    )


def source_for(payload: GenerateAiText, restricted: bool) -> str:
    title = reviewed_text(payload.title)
    summary = "" if restricted else reviewed_text(payload.summary)
    body = "" if restricted else reviewed_text(payload.body)
    if not (title or summary or body):
        raise AuthFailure(422, 42289, "请先填写可以用于生成的内容")
    return "\n\n".join(
        f"{label}\n{value}"
        for label, value in (("标题", title), ("摘要或简介", summary), ("正文节选", body))
        if value
    )


def request_view(config: Settings, row: AiWritingRequest) -> AiGeneration:
    if (row.finished_at or row.created_at) < now() - RESULT_TTL:
        raise AuthFailure(404, 40489, "生成请求已过期")
    status, error = cast(Status, row.status), row.error
    output = None
    if row.output_ciphertext:
        try:
            output = (
                cipher(config, f"request:{row.id}").decrypt(row.output_ciphertext.encode()).decode()
            )
        except (InvalidToken, UnicodeError):
            status, error = "failed", "configuration_changed"
    return AiGeneration(id=row.id, status=status, text=output, error=error)


async def enqueue(
    session: AsyncSession,
    config: Settings,
    actor: Actor,
    payload: TestAiConnection | GenerateAiText,
) -> AiGeneration:
    test = not isinstance(payload, GenerateAiText)
    if test and not actor.allows("settings:write"):
        raise AuthFailure(403, 40389, "没有测试模型配置的权限")

    # Serialize cost limits and retries on the private singleton, not the site settings row.
    settings = await configuration(session, locked=True)
    scene = field = None
    resource_id = None
    restricted = False
    if isinstance(payload, GenerateAiText):
        scene, field, resource_id = payload.scene, payload.field, payload.resource_id
        server_restricted = await target_access(session, config, actor, scene, field, resource_id)
        restricted = payload.restricted or server_restricted
        source_for(payload, restricted)
    # Preserve the request fingerprint already stored for requests queued before providers existed.
    exclude = {"provider_id"} if payload.provider_id is None else set()
    if test and payload.expected_revision is None:
        exclude.add("expected_revision")
    fingerprint = hashlib.sha256(payload.model_dump_json(exclude=exclude).encode()).hexdigest()
    previous = await session.get(AiWritingRequest, payload.request_id)
    if previous:
        if previous.actor_id != actor.id or previous.fingerprint != fingerprint:
            raise AuthFailure(409, 40989, "生成请求编号已被使用")
        return request_view(config, previous)
    if not test and not settings.enabled:
        raise AuthFailure(503, 50389, "AI 写作助手尚未启用或完成配置")
    if payload.expected_revision is not None and payload.expected_revision != settings.revision:
        raise AuthFailure(409, 40989, "AI 写作配置已修改，请重新检查生成材料")
    provider_id = payload.provider_id or settings.default_provider_id
    if configured_provider(settings, provider_id) is None:
        if payload.provider_id:
            raise AuthFailure(422, 42289, "所选服务商未配置")
        raise AuthFailure(503, 50389, "AI 写作助手尚未启用或完成配置")
    if scene and field not in settings.fields.get(scene, []):
        raise AuthFailure(403, 40389, "未启用此字段的 AI 写作")
    current = now()
    owner = AiWritingRequest.actor_id == actor.id
    active = AiWritingRequest.status.in_(("queued", "running"))
    counts = (
        await session.execute(
            select(
                func.count().filter(
                    owner, AiWritingRequest.created_at >= current - timedelta(minutes=1)
                ),
                func.count().filter(
                    owner, active, AiWritingRequest.created_at >= current - QUEUED_TTL
                ),
                func.count().filter(active, AiWritingRequest.created_at >= current - QUEUED_TTL),
                func.count().filter(AiWritingRequest.created_at >= current - timedelta(days=1)),
            ).select_from(AiWritingRequest)
        )
    ).one()
    if counts[0] >= 5 or counts[1] >= 1 or counts[2] >= 8 or counts[3] >= 200:
        raise AuthFailure(429, 42989, "生成请求过多，请稍后重试")

    encrypted = (
        cipher(config, f"request:{payload.request_id}")
        .encrypt(
            # Store title separately so a visibility change can still discard the optional context.
            (
                payload.model_copy(update={"body": "", "summary": ""}).model_dump_json()
                if isinstance(payload, GenerateAiText) and restricted
                else payload.model_dump_json()
            ).encode()
        )
        .decode()
    )
    row = AiWritingRequest(
        id=payload.request_id,
        actor_id=actor.id,
        revision=settings.revision,
        fingerprint=fingerprint,
        provider_id=provider_id,
        scene=scene,
        field=field,
        resource_id=resource_id,
        input_ciphertext=encrypted,
        created_at=current,
        status="queued",
    )
    session.add(row)
    return request_view(config, row)
