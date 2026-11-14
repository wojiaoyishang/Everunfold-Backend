"""Run explicit, leased model requests after their admission transaction commits."""

from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import delete, select, update

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.service import available
from app.modules.auth.models import User
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure

from .models import AiWritingRequest
from .safety import cipher
from .schemas import PLUGIN_ID, TARGETS, GenerateAiText, Scene
from .service import (
    QUEUED_TTL,
    RESULT_TTL,
    configuration,
    configured_provider,
    now,
    source_for,
    target_access,
)
from .transport import REQUEST_TIMEOUT, ProviderError, generate


@dataclass(frozen=True)
class Job:
    id: UUID
    base_url: str
    model: str
    key: str
    language: str
    task: str
    source: str
    maximum: int
    single_line: bool


async def claim(db: Database, config: Settings) -> tuple[Job | None, bool]:
    async with db.session() as session, session.begin():
        current = now()
        await session.execute(
            update(AiWritingRequest)
            .where(AiWritingRequest.status == "running", AiWritingRequest.lease_until < current)
            .values(status="unknown", error="interrupted", finished_at=current)
        )
        await session.execute(
            update(AiWritingRequest)
            .where(
                AiWritingRequest.status == "queued",
                AiWritingRequest.created_at < current - QUEUED_TTL,
            )
            .values(
                status="failed", error="queue_timeout", finished_at=current, input_ciphertext=None
            )
        )
        await session.execute(
            update(AiWritingRequest)
            .where(
                AiWritingRequest.finished_at < current - RESULT_TTL,
                AiWritingRequest.output_ciphertext.is_not(None),
            )
            .values(output_ciphertext=None)
        )
        # Keep only non-content counters for the daily budget and retry window.
        await session.execute(
            delete(AiWritingRequest).where(
                AiWritingRequest.finished_at < current - timedelta(days=1)
            )
        )
        if PLUGIN_ID not in await available(session):
            return None, False
        settings = await configuration(session, locked=True)
        row = await session.scalar(
            select(AiWritingRequest)
            .where(AiWritingRequest.status == "queued")
            .order_by(AiWritingRequest.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None, False
        error = "configuration_changed"
        job = None
        try:
            user = await session.get(User, row.actor_id)
            provider = configured_provider(settings, row.provider_id)
            if row.revision == settings.revision and row.input_ciphertext and provider:
                if user is None or not user.active or user.must_change_password:
                    raise AuthFailure(403, 40389, "没有使用 AI 写作助手的权限")
                actor = Actor.browser(user)
                language, task, source, maximum, single = (
                    "en",
                    "Return OK",
                    "Connection check.",
                    12,
                    True,
                )
                if row.scene is not None:
                    scene = cast(Scene, row.scene)
                    assert row.field is not None
                    if not settings.enabled or row.field not in settings.fields.get(scene, []):
                        raise AuthFailure(403, 40389, "未启用此字段的 AI 写作")
                    crypto = cipher(config, f"request:{row.id}")
                    payload = GenerateAiText.model_validate_json(
                        crypto.decrypt(row.input_ciphertext.encode())
                    )
                    server_restricted = await target_access(
                        session, config, actor, scene, row.field, row.resource_id
                    )
                    source = source_for(payload, payload.restricted or server_restricted)
                    maximum, task = TARGETS[scene][row.field]
                    language = payload.language
                    single = row.field in {"seo_title", "seo_share_title"}
                elif not actor.allows("settings:write"):
                    raise AuthFailure(403, 40389, "没有测试模型配置的权限")
                key = ""
                if provider["key_ciphertext"]:
                    key = (
                        cipher(config, "key").decrypt(provider["key_ciphertext"].encode()).decode()
                    )
                job = Job(
                    row.id,
                    provider["base_url"],
                    provider["model"],
                    key,
                    language,
                    task,
                    source,
                    maximum,
                    single,
                )
        except AuthFailure:
            error = "access_changed"
        except (InvalidToken, ValidationError, UnicodeError):
            error = "configuration_changed"
        row.input_ciphertext = None
        if job is None:
            row.status, row.error, row.finished_at = "failed", error, current
        else:
            row.status = "running"
            row.lease_until = current + timedelta(seconds=REQUEST_TIMEOUT + 12)
        return job, True


async def deliver(config: Settings) -> dict[str, int]:
    db = Database(config.database.url.get_secret_value())
    try:
        job, processed = await claim(db, config)
        if job is None:
            return {"processed": int(processed)}
        encrypted, error = None, None
        status = "succeeded"
        try:
            result = await generate(
                base_url=job.base_url,
                model=job.model,
                key=job.key,
                language=job.language,
                task=job.task,
                source=job.source,
                maximum=job.maximum,
                single_line=job.single_line,
            )
            encrypted = cipher(config, f"request:{job.id}").encrypt(result.encode()).decode()
        except ProviderError as exc:
            error = str(exc)
            status = "unknown" if error == "timeout" else "failed"
        async with db.session() as session, session.begin():
            await session.execute(
                update(AiWritingRequest)
                .where(AiWritingRequest.id == job.id, AiWritingRequest.status == "running")
                .values(status=status, error=error, output_ciphertext=encrypted, finished_at=now())
            )
        return {"processed": 1}
    finally:
        await db.close()
