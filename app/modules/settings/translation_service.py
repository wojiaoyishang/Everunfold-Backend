"""Database overrides layered over immutable program dictionaries."""

import hashlib
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.i18n import i18n
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.defaults import public_settings
from app.modules.settings.models import (
    SettingsAudit,
    TranslationOverride,
    TranslationRevision,
)
from app.modules.settings.translation_schemas import (
    SaveTranslations,
    TranslationCatalog,
    TranslationCatalogEntry,
    TranslationOverrideItem,
    TranslationOverrides,
    TranslationScope,
    TranslationState,
)

locale_pattern = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
placeholder_pattern = re.compile(r"(?<!\{)\{([a-zA-Z_]\w*)\}(?!\})")


def source_hash(source: str) -> str:
    return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()


def placeholders(value: str) -> Counter[str]:
    return Counter(placeholder_pattern.findall(value))


async def refresh_translation_cache(session: AsyncSession) -> None:
    rows = (
        await session.scalars(
            select(TranslationOverride).where(TranslationOverride.scope == "backend")
        )
    ).all()
    values: dict[str, dict[str, str]] = {}
    for row in rows:
        values.setdefault(row.locale, {})[row.message_key] = row.value
    i18n.replace_overrides("backend", values)


class TranslationService:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session = session
        self.config = config

    async def revision(self, *, locked: bool = False) -> TranslationRevision:
        statement = select(TranslationRevision).where(TranslationRevision.id == 1)
        row = await self.session.scalar(statement.with_for_update() if locked else statement)
        if row is None:
            row = TranslationRevision(id=1, revision=1)
            self.session.add(row)
            await self.session.flush()
        return row

    async def overrides(self) -> TranslationOverrides:
        rows = (
            await self.session.scalars(
                select(TranslationOverride).order_by(
                    TranslationOverride.scope,
                    TranslationOverride.locale,
                    TranslationOverride.message_key,
                )
            )
        ).all()
        values: dict[str, dict[str, dict[str, str]]] = {
            "public": {},
            "admin": {},
            "backend": {},
        }
        for row in rows:
            values[row.scope].setdefault(row.locale, {})[row.message_key] = row.value
        return TranslationOverrides.model_validate(values)

    def backend_catalog(self) -> list[TranslationCatalogEntry]:
        return [
            TranslationCatalogEntry(
                scope="backend",
                key=key,
                owner=key.split(".", 1)[0],
                source=defaults.get("zh-CN", key),
                defaults=defaults,
            )
            for key, defaults in sorted(i18n.catalog().items())
        ]

    async def catalog(self) -> TranslationCatalog:
        revision = await self.revision()
        return TranslationCatalog(revision=revision.revision, items=self.backend_catalog())

    async def state(self) -> TranslationState:
        revision = await self.revision()
        rows = (
            await self.session.scalars(
                select(TranslationOverride).order_by(
                    TranslationOverride.scope,
                    TranslationOverride.locale,
                    TranslationOverride.message_key,
                )
            )
        ).all()
        return TranslationState(
            revision=revision.revision,
            overrides=[
                TranslationOverrideItem(
                    id=row.id,
                    scope=cast(TranslationScope, row.scope),
                    key=row.message_key,
                    owner=row.owner,
                    locale=row.locale,
                    source=row.source,
                    source_hash=row.source_hash,
                    value=row.value,
                    revision=row.revision,
                    updated_at=row.updated_at,
                )
                for row in rows
            ],
            backend_catalog=self.backend_catalog(),
        )

    async def save(self, payload: SaveTranslations, actor: uuid.UUID) -> TranslationState:
        from app.modules.settings.service import SettingsService

        service = SettingsService(self.session, self.config)
        await lock_references(self.session)
        site = await service.site(locked=True)
        state = await self.revision(locked=True)
        if state.revision != payload.expected_revision:
            raise AuthFailure(409, 40939, "翻译已被修改，请重新读取后合并")
        available = {
            item.code
            for item in public_settings(site.data).site.language_catalog.languages
            if item.enabled
        } | {"zh-CN", "en"}
        backend_keys = set(i18n.catalog())
        existing = {
            (row.scope, row.locale, row.message_key): row
            for row in (await self.session.scalars(select(TranslationOverride))).all()
        }
        seen: set[tuple[str, str, str]] = set()
        changed = 0
        now = datetime.now(UTC)
        for change in payload.changes:
            if not locale_pattern.fullmatch(change.locale) or change.locale not in available:
                raise AuthFailure(422, 42299, "翻译语言无效或尚未启用")
            source = change.source.strip()
            if not source or any(ord(char) < 32 and char not in "\n\t" for char in source):
                raise AuthFailure(422, 42299, "翻译原文无效")
            if change.scope == "backend" and change.key not in backend_keys:
                raise AuthFailure(422, 42299, "后端消息键不存在")
            identity = (change.scope, change.locale, change.key)
            if identity in seen:
                raise AuthFailure(422, 42299, "同一翻译项不能重复提交")
            seen.add(identity)
            value = (change.value or "").strip()
            if value and placeholders(value) != placeholders(source):
                raise AuthFailure(422, 42299, "翻译占位符必须与原文一致")
            row = existing.get(identity)
            if not value:
                if row is not None:
                    await self.session.delete(row)
                    changed += 1
                continue
            owner = change.key.split(".", 1)[0] if change.scope == "backend" else "frontend"
            if row is None:
                row = TranslationOverride(
                    id=uuid.uuid4(),
                    scope=change.scope,
                    owner=owner,
                    message_key=change.key,
                    locale=change.locale,
                    source=source,
                    source_hash=source_hash(source),
                    value=value,
                    revision=state.revision + 1,
                    updated_by=actor,
                    updated_at=now,
                )
                self.session.add(row)
                existing[identity] = row
                changed += 1
            elif row.value != value or row.source != source or row.owner != owner:
                row.value = value
                row.source = source
                row.source_hash = source_hash(source)
                row.owner = owner
                row.revision = state.revision + 1
                row.updated_by = actor
                row.updated_at = now
                changed += 1
        if not changed:
            await self.session.rollback()
            return await self.state()
        state.revision += 1
        site.generation += 1
        self.session.add(
            SettingsAudit(
                user_id=actor,
                resource="translations",
                revision=state.revision,
                changes={
                    "changed": changed,
                    "public_settings_revision": site.generation,
                },
            )
        )
        await self.session.flush()
        resolved = await service.resolve(public_settings(site.data))
        for node, frontend in self.config.frontends.items():
            if frontend.enabled and frontend.settings_receiver_url:
                await service.enqueue(
                    node,
                    site.source_epoch,
                    "public-settings",
                    site.generation,
                    resolved,
                )
        await self.session.commit()
        await refresh_translation_cache(self.session)
        return await self.state()
