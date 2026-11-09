"""Version-checked template drafts and application through the existing durable settings stream."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins.state import shortcode_items
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.defaults import public_settings
from app.modules.settings.models import SettingsAudit
from app.modules.settings.schemas import SaveSettings
from app.modules.settings.service import SettingsService
from app.modules.shortcodes.compiler import compile_shortcode
from app.modules.shortcodes.models import ShortcodeDefinition, ShortcodeVersion
from app.modules.shortcodes.registry import builtin_catalog
from app.modules.shortcodes.schemas import (
    PreviewShortcode,
    PublicShortcode,
    RestoreShortcode,
    SaveShortcode,
    ShortcodeAction,
    ShortcodeCatalog,
    ShortcodeItem,
    ShortcodeSource,
)


class ShortcodeService:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session = session
        self.settings = SettingsService(session, config)

    def compile(self, source: ShortcodeSource, identity: str, revision: int) -> PublicShortcode:
        try:
            return compile_shortcode(source, identity, revision)
        except ValueError as error:
            raise AuthFailure(422, 42280, str(error)) from error

    async def item(self, row: ShortcodeDefinition) -> ShortcodeItem:
        source = ShortcodeSource.model_validate(row.data)
        versions = list(
            (
                await self.session.scalars(
                    select(ShortcodeVersion.revision)
                    .where(ShortcodeVersion.definition_id == row.id)
                    .order_by(ShortcodeVersion.revision.desc())
                )
            ).all()
        )
        compiled = self.compile(source, str(row.id), row.revision)
        value = public_settings((await self.settings.site()).data)
        value.shortcodes = [compiled]
        compiled = (await self.settings.resolve(value)).shortcodes[0]
        return ShortcodeItem(
            id=str(row.id),
            origin="custom",
            editable=True,
            revision=row.revision,
            applied_revision=row.applied_revision,
            archived=row.archived,
            source=source,
            compiled=compiled,
            versions=versions,
        )

    async def catalog(self) -> ShortcodeCatalog:
        site = await self.settings.site()
        compiled = {
            item.id: item
            for item in (await self.settings.resolve(public_settings(site.data))).shortcodes
        }
        backend = [
            item.model_copy(update={"compiled": compiled[item.id]})
            for item in shortcode_items(site.data)
        ]
        rows = (
            await self.session.scalars(
                select(ShortcodeDefinition).order_by(ShortcodeDefinition.name)
            )
        ).all()
        return ShortcodeCatalog(
            items=backend + [await self.item(row) for row in rows],
            applied=list(compiled.values()),
            settings_revision=site.generation,
        )

    async def locked(self, identity: str, revision: int) -> ShortcodeDefinition:
        try:
            key = uuid.UUID(identity)
        except ValueError as error:
            raise AuthFailure(404, 40480, "短代码不存在") from error
        row = await self.session.get(ShortcodeDefinition, key, with_for_update=True)
        if row is None:
            if any(item.id == identity for item in builtin_catalog()):
                raise AuthFailure(403, 40380, "代码注册的短代码不可编辑")
            raise AuthFailure(404, 40480, "短代码不存在或由前端代码注册")
        if row.revision != revision:
            raise AuthFailure(409, 40980, "短代码已被修改，请先读取服务器版本")
        return row

    def audit(self, row: ShortcodeDefinition, actor: uuid.UUID, action: str) -> None:
        self.session.add(
            SettingsAudit(
                user_id=actor,
                resource="shortcode:" + str(row.id),
                revision=row.revision,
                changes={"action": action, "source": row.data},
            )
        )

    async def save(self, payload: SaveShortcode, actor: uuid.UUID) -> ShortcodeItem:
        if not payload.value.name.startswith("custom:"):
            raise AuthFailure(422, 42280, "自定义短代码名称必须以 custom: 开头")
        await lock_references(self.session)
        await self.settings.site(locked=True)
        identity = payload.id or str(uuid.uuid4())
        compiled = self.compile(payload.value, identity, max(1, payload.expected_revision + 1))
        check = public_settings((await self.settings.site()).data)
        check.shortcodes = [compiled]
        await self.settings.resolve(check)
        if payload.id:
            row = await self.locked(payload.id, payload.expected_revision)
            if row.name != payload.value.name:
                raise AuthFailure(422, 42280, "调用名称创建后不能修改")
            if row.applied_revision is not None and row.data.get("kind") != payload.value.kind:
                raise AuthFailure(422, 42280, "已应用模板的调用类型不能修改")
            if row.data != payload.value.model_dump():
                row.revision += 1
                row.data = payload.value.model_dump()
                row.updated_at = datetime.now(UTC)
        else:
            if payload.expected_revision != 0:
                raise AuthFailure(409, 40980, "新建短代码的期望版本必须为 0")
            rows = (await self.session.scalars(select(ShortcodeDefinition))).all()
            if len(rows) >= 100:
                raise AuthFailure(422, 42280, "最多保存 100 个自定义短代码")
            if any(row.name == payload.value.name for row in rows):
                raise AuthFailure(409, 40981, "调用名称已存在")
            row = ShortcodeDefinition(
                id=uuid.UUID(compiled.id),
                name=payload.value.name,
                revision=1,
                archived=False,
                data=payload.value.model_dump(),
            )
            self.session.add(row)
        self.audit(row, actor, "save")
        await self.session.commit()
        return await self.item(row)

    async def apply(self, payload: ShortcodeAction, actor: uuid.UUID) -> ShortcodeCatalog:
        await lock_references(self.session)
        site = await self.settings.site(locked=True)
        row = await self.locked(payload.id, payload.expected_revision)
        value = public_settings(site.data)
        compiled = self.compile(ShortcodeSource.model_validate(row.data), str(row.id), row.revision)
        value.shortcodes = [item for item in value.shortcodes if item.id != str(row.id)] + [
            compiled
        ]
        value.shortcodes.sort(key=lambda item: item.name)
        exists = await self.session.scalar(
            select(ShortcodeVersion).where(
                ShortcodeVersion.definition_id == row.id, ShortcodeVersion.revision == row.revision
            )
        )
        if exists is None:
            self.session.add(
                ShortcodeVersion(definition_id=row.id, revision=row.revision, data=row.data)
            )
        row.applied_revision = row.revision
        row.archived = False
        self.audit(row, actor, "apply")
        # Definitions, immutable version, audit and node outbox commit together.
        await self.settings.save(
            SaveSettings(expected_revision=site.generation, value=value),
            actor,
            shortcode_write=True,
        )
        return await self.catalog()

    async def archive(self, payload: ShortcodeAction, actor: uuid.UUID) -> ShortcodeCatalog:
        await self.settings.site(locked=True)
        row = await self.locked(payload.id, payload.expected_revision)
        # Archiving hides the insertion option; existing published calls keep rendering.
        if not row.archived:
            row.archived = True
            row.revision += 1
        self.audit(row, actor, "archive")
        await self.session.commit()
        return await self.catalog()

    async def restore(self, payload: RestoreShortcode, actor: uuid.UUID) -> ShortcodeItem:
        await lock_references(self.session)
        await self.settings.site(locked=True)
        row = await self.locked(payload.id, payload.expected_revision)
        version = await self.session.scalar(
            select(ShortcodeVersion).where(
                ShortcodeVersion.definition_id == row.id,
                ShortcodeVersion.revision == payload.version,
            )
        )
        if version is None:
            raise AuthFailure(404, 40480, "应用版本不存在")
        row.data = version.data
        row.revision += 1
        row.archived = False
        self.audit(row, actor, "restore_draft")
        await self.session.commit()
        return await self.item(row)

    async def preview(self, payload: PreviewShortcode) -> PublicShortcode:
        # Preview identities are bounded and never persist or overwrite a registered definition.
        compiled = self.compile(
            payload.value,
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, "everunfold:preview:" + (payload.id or payload.value.name)
                )
            ),
            1,
        )
        value = public_settings((await self.settings.site()).data)
        value.shortcodes = [compiled]
        return (await self.settings.resolve(value)).shortcodes[0]
