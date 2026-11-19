"""One transaction per edit: optimistic version, durable retry receipt and full audit snapshot."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins.business_events import BusinessAction, BusinessEvent
from app.core.plugins.business_http import mark_attempt, mark_committed
from app.modules.auth.permissions import AuthSubject
from app.modules.auth.security import hash_password
from app.modules.auth.service import AuthFailure
from app.modules.content.content_paths import initial_path
from app.modules.content.draft_models import (
    ContentDraft,
    DraftReceipt,
    DraftRevision,
    TranslationGroup,
)
from app.modules.content.draft_repository import DraftRepository, detail
from app.modules.content.draft_schemas import (
    ContentActions,
    ContentEditAction,
    DraftCreate,
    DraftDetail,
    DraftFields,
    DraftMutation,
    DraftSave,
    DraftSchedule,
)
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.models import Content, SiteSettings, Taxonomy
from app.modules.content.publication import ContentPublication, publish_state
from app.modules.content.type_registry import ContentTypeDefinition, content_types
from app.modules.media.locking import lock_references
from app.modules.media.models import MediaAsset
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.languages import require_language


def media_sources(row: ContentDraft) -> str:
    return json.dumps(row.component_document if row.edit_mode == "components" else row.body)


def require_block_mode(row: ContentDraft) -> None:
    if row.edit_mode == "components":
        raise AuthFailure(409, 40973, "此内容使用组件模式，请在实时编辑器中修改")


Action = Literal[
    "create", "save", "trash", "restore", "publish", "withdraw", "schedule", "cancel_schedule"
]


def require_type(type_id: str) -> ContentTypeDefinition:
    definition = content_types.get(type_id)
    if definition is None:
        raise AuthFailure(422, 42250, "此内容类型当前不可用，已有资料仍会保留")
    return definition


class DraftService:
    def __init__(self, session: AsyncSession, config: Settings | None = None) -> None:
        self.session = session
        self.config = config
        self.repo = DraftRepository(session)

    async def public_actions(self, content_id: UUID) -> ContentActions:
        """Called only after the same current-account authorization as the editor itself."""
        row = await self.repo.find(content_id)
        published = await self.session.get(Content, content_id)
        if (
            row is None
            or row.deleted_at is not None
            or row.published_revision is None
            or content_types.get(row.type_id) is None
            or published is None
            or published.publication_status != "published"
        ):
            return ContentActions()
        return ContentActions(edit=ContentEditAction(id=row.id, type_id=row.type_id))

    async def read(self, draft_id: UUID) -> DraftDetail:
        row = await self.repo.find(draft_id)
        if row is None:
            raise AuthFailure(404, 40450, "找不到这份草稿")
        result = detail(row)
        from app.modules.content.collaboration.models import CollaborationDocument

        head = await self.session.get(CollaborationDocument, row.id)
        result.collaboration_seq = head.committed_seq if head else None
        result.media_urls = await editor_media(
            self.session, self.config, media_sources(row), result.settings, strict=False
        )
        return result

    async def mutate(
        self,
        action: Action,
        payload: DraftCreate | DraftSave | DraftMutation,
        actor: AuthSubject,
        *,
        demo: bool = False,
        collaboration_cutoff: tuple[UUID, int] | None = None,
        note: str = "",
        live_fields: DraftFields | None = None,
        commit: bool = True,
        component_editor: bool = False,
    ) -> DraftDetail:
        event_action: BusinessAction | None = None
        if action in ("publish", "withdraw", "schedule"):
            event_action = action
        elif action == "cancel_schedule":
            event_action = "schedule_cancel"
        serialized = payload.model_dump(mode="json")
        if isinstance(payload, DraftCreate | DraftSave) and payload.fields.access_password:
            password = payload.fields.access_password
            # Use a memory-hard fingerprint instead of a cheap password oracle.
            serialized["fields"]["access_password"] = await to_thread.run_sync(
                lambda: hashlib.scrypt(
                    password.encode(),
                    salt=payload.operation_id.bytes,
                    n=65536,
                    r=8,
                    p=1,
                    maxmem=128 * 1024 * 1024,
                ).hex()
            )
        digest_input: dict[str, object] = {"action": action, "payload": serialized, "demo": demo}
        if component_editor:
            digest_input["component_editor"] = True
        if collaboration_cutoff is not None:
            digest_input["collaboration_cutoff"] = str(collaboration_cutoff)
        if note:
            digest_input["note"] = note
        digest = hashlib.sha256(
            json.dumps(
                digest_input,
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        # Same lock order as settings/media writers. Also serializes retry receipts across replicas.
        await lock_references(self.session)
        receipt = await self.session.get(DraftReceipt, payload.operation_id)
        if receipt:
            if receipt.actor_id != actor.id or receipt.digest != digest:
                raise AuthFailure(409, 40950, "操作标识已用于另一项修改，请重新操作")
            return DraftDetail.model_validate(receipt.response)
        now = datetime.now(UTC)
        if isinstance(payload, DraftCreate):
            selected_language = await require_language(self.session, payload.language)
            row = ContentDraft(
                id=uuid4(),
                language=selected_language.code,
                type_id=payload.type_id,
                title="",
                excerpt="",
                body=[],
                edit_mode="blocks",
                editor_settings={},
                author=actor.display_name,
                created_by=actor.id,
                revision=1,
                is_demo=demo,
                created_at=now,
                updated_at=now,
            )
            row.translation_group_id = row.id
            self.session.add(
                TranslationGroup(id=row.id, source_id=row.id, type_id=row.type_id, revision=1)
            )
            self.session.add(row)
        else:
            existing = await self.repo.find(payload.id, lock=True)
            if existing is None:
                raise AuthFailure(404, 40450, "找不到这份草稿")
            row = existing
            if event_action:
                mark_attempt(event_action, resource_kind=row.type_id)
            from app.modules.content.merge_lock import require_unlocked

            await require_unlocked(self.session, row.id)
            if row.revision != payload.expected_revision:
                raise AuthFailure(409, 40951, "内容已在其他地方修改，请保留输入并重新读取后再保存")
            row.revision += 1
            row.updated_at = now
        publish_outcome = publish_state(row)
        was_scheduled = row.scheduled_for is not None
        definition = require_type(row.type_id)
        if isinstance(payload, DraftCreate | DraftSave) and action != "cancel_schedule":
            if row.deleted_at:
                raise AuthFailure(409, 40952, "请先从回收站恢复，再编辑")
            fields = payload.fields
            if fields.edit_mode == "components" and not component_editor:
                raise AuthFailure(409, 40973, "请在实时编辑器中启用组件模式")
            if not component_editor:
                require_block_mode(row)
            if (
                component_editor
                and fields.edit_mode == "blocks"
                and (action != "save" or row.edit_mode != "components")
            ):
                raise AuthFailure(409, 40973, "此操作需要组件模式")

            from app.modules.content.collaboration.models import CollaborationDocument

            head = None
            if isinstance(payload, DraftSave) and collaboration_cutoff is None:
                head = await self.session.get(CollaborationDocument, row.id, with_for_update=True)
                if head is not None and row.edit_mode != "components":
                    if not component_editor:
                        raise AuthFailure(409, 40972, "此草稿已启用协同，请使用协同编辑器保存")
                    if payload.expected_collaboration_seq != head.committed_seq:
                        raise AuthFailure(409, 40972, "协同内容已更新，请刷新后切换编辑模式")
            if definition.title_required and not fields.title.strip():
                raise AuthFailure(422, 42251, "请填写标题")
            if not definition.supports_excerpt and fields.excerpt:
                raise AuthFailure(422, 42252, "此内容类型不使用独立摘要")
            source_blocks = [block.model_dump(mode="json") for block in fields.body]
            component_document = fields.component_document
            source = (
                component_document.model_dump_json()
                if component_document is not None
                else json.dumps(source_blocks)
            )
            await self._check_media(source)
            settings = fields.settings or EditorSettings.model_validate(row.editor_settings or {})
            if isinstance(payload, DraftCreate) and row.type_id == "article":
                from app.modules.settings.defaults import public_settings

                site = await self.session.get(SiteSettings, 1)
                defaults = public_settings(site.data if site else {}).site.writing
                changes: dict[str, bool | list[str]] = {}
                if "comments_open" not in settings.model_fields_set:
                    changes["comments_open"] = defaults.comments_open
                if "categories" not in settings.model_fields_set and defaults.default_category_id:
                    term = await self.session.get(Taxonomy, defaults.default_category_id)
                    if term and term.kind == "category" and not term.retired:
                        changes["categories"] = [term.name]
                settings = settings.model_copy(update=changes)
            from app.modules.content.taxonomy_names import TermDirectory, register_draft_terms

            settings = (await TermDirectory.load(self.session)).normalize(settings)
            await register_draft_terms(self.session, settings)
            if not settings.path:
                settings.path = row.public_path or await initial_path(
                    self.session,
                    fields.title,
                    row.id,
                    language=row.language,
                    type_id=row.type_id,
                    date=settings.display_published_at or now,
                )
            if fields.access_password:
                row.access_password_hash = await to_thread.run_sync(
                    hash_password, fields.access_password
                )
            if settings.visibility == "password" and not row.access_password_hash:
                raise AuthFailure(422, 42259, "请先设置文章密码")
            if settings.visibility == "public" and settings.opens_at is not None:
                raise AuthFailure(422, 42259, "定时开放需要先选择密码保护")
            await editor_media(self.session, self.config, source, settings)
            row.title, row.excerpt = fields.title.strip(), fields.excerpt.strip()
            if fields.edit_mode == "blocks":
                row.body = source_blocks
                if row.edit_mode == "components" and head is not None:
                    from app.modules.content.collaboration.epochs import replace_epoch

                    await replace_epoch(
                        self.session, head, fields.model_copy(update={"settings": settings})
                    )
            elif component_document is not None:
                row.component_document = component_document.model_dump(mode="json")
                if isinstance(payload, DraftCreate):
                    row.body = source_blocks or [
                        {"id": str(uuid4()), "type": "markdown", "content": ""}
                    ]
            row.edit_mode = fields.edit_mode
            row.editor_settings = settings.model_dump(mode="json")
        elif action == "trash":
            if row.deleted_at:
                raise AuthFailure(409, 40952, "内容已在回收站中")
            row.deleted_at = now
        elif action == "restore":
            if not row.deleted_at:
                raise AuthFailure(409, 40952, "内容已经恢复")
            row.deleted_at = None
        from app.modules.content.scheduled_publication import clear_schedule, schedule_publication

        if action == "schedule":
            if not isinstance(payload, DraftSchedule) or self.config is None:
                raise RuntimeError("Scheduled publication needs a dated command and configuration")
            await schedule_publication(self.session, self.config, row, payload, actor)
        elif action in {"cancel_schedule", "publish", "withdraw", "trash"}:
            clear_schedule(row)
        if action in {"publish", "withdraw"} or (action == "trash" and row.published_revision):
            if self.config is None:
                raise RuntimeError("Publication requires configured delivery targets")
            publication = ContentPublication(self.session, self.config)
            change: CacheChange | None
            if action == "publish":
                change = await publication.publish(row)
            else:
                change = await publication.withdraw(row)
            if change is not None:
                await publication.stage(change)
        result = detail(row)
        result.media_urls = await editor_media(
            self.session, self.config, media_sources(row), result.settings, strict=False
        )
        snapshot = result.model_dump(mode="json")
        try:
            await self.session.flush()
            self.session.add(
                DraftRevision(
                    draft_id=row.id,
                    revision=row.revision,
                    actor_id=actor.id,
                    action=action,
                    snapshot=snapshot,
                    created_at=now,
                    note=note,
                    epoch=collaboration_cutoff[0] if collaboration_cutoff else None,
                    committed_seq=collaboration_cutoff[1] if collaboration_cutoff else None,
                    parents=[row.revision - 1] if row.revision > 1 else [],
                )
            )
            self.session.add(
                DraftReceipt(
                    id=payload.operation_id,
                    actor_id=actor.id,
                    digest=digest,
                    draft_id=row.id,
                    response=snapshot,
                    created_at=now,
                )
            )
            if live_fields is not None:
                # A checkpoint captures its exact cutoff; reading the working head
                # must still include edits accepted after that cutoff.
                row.title, row.excerpt = live_fields.title, live_fields.excerpt
                row.body = [block.model_dump(mode="json") for block in live_fields.body]
                live_settings = (await TermDirectory.load(self.session)).normalize(
                    live_fields.settings or EditorSettings()
                )
                saved_settings = EditorSettings.model_validate(row.editor_settings)
                live_settings.visibility, live_settings.opens_at = (
                    saved_settings.visibility,
                    saved_settings.opens_at,
                )
                row.editor_settings = live_settings.model_dump(mode="json")
            if commit:
                await self.session.commit()
                if (
                    event_action is not None
                    and (event_action != "withdraw" or publish_outcome == "update")
                    and (event_action != "schedule_cancel" or was_scheduled)
                ):
                    mark_committed(
                        BusinessEvent(
                            event_action,
                            "success",
                            stage="manual",
                            resource_kind=row.type_id,
                            resource_id=row.id,
                            state=publish_outcome if action == "publish" else "",
                        )
                    )
            else:
                await self.session.flush()
        except Exception:
            await self.session.rollback()
            raise
        return result

    async def _check_media(self, body: str) -> None:
        ids = {
            UUID(value)
            for value in re.findall(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", body)
        }
        if not ids:
            return
        unavailable = await self.session.scalar(
            select(MediaAsset.id)
            .where(
                MediaAsset.id.in_(ids),
                (MediaAsset.removed_at.is_not(None))
                | (MediaAsset.status != "ready")
                | (MediaAsset.deleted_at.is_not(None))
                | MediaAsset.purge_pending,
            )
            .limit(1)
        )
        if unavailable:
            raise AuthFailure(409, 40953, "正文引用的文件已被删除或尚未上传完成")
