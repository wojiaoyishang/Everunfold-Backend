"""Transactional language membership; shared text stays in each independent Y.Doc."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.permissions import AuthSubject
from app.modules.auth.service import AuthFailure
from app.modules.content.component_document import ComponentDocument
from app.modules.content.content_paths import initial_path
from app.modules.content.draft_models import (
    ContentDraft,
    DraftReceipt,
    DraftRevision,
    TranslationGroup,
)
from app.modules.content.draft_repository import summary
from app.modules.content.draft_schemas import (
    TranslationCatalog,
    TranslationCreate,
    TranslationGroupMutation,
    TranslationLink,
    TranslationMember,
    TranslationMutation,
)
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.merge_lock import require_unlocked
from app.modules.content.models import Content
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.languages import require_language
from app.modules.settings.models import SettingsAudit


class Translations:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session, self.config = session, config

    async def read(self, identity: UUID) -> TranslationCatalog:
        row = await self.session.get(ContentDraft, identity)
        if row is None:
            raise AuthFailure(404, 40450, "找不到这份草稿")
        group = await self.session.get(TranslationGroup, row.translation_group_id)
        if group is None:
            raise AuthFailure(404, 40450, "找不到这份草稿")
        members = list(
            await self.session.scalars(
                select(ContentDraft)
                .where(ContentDraft.translation_group_id == group.id)
                .order_by(ContentDraft.created_at, ContentDraft.id)
            )
        )
        source = next(item for item in members if item.id == group.source_id)
        return TranslationCatalog(
            id=group.id,
            source_id=source.id,
            type_id=group.type_id,
            revision=group.revision,
            members=[
                TranslationMember(
                    **summary(item).model_dump(),
                    is_original=item.id == source.id,
                    needs_review=item.translated_from_revision is not None
                    and item.translated_from_revision
                    < (source.published_revision or source.revision),
                )
                for item in members
            ],
        )

    async def mutate(
        self, action: str, payload: TranslationMutation, actor: AuthSubject
    ) -> TranslationCatalog:
        digest = hashlib.sha256(
            json.dumps(
                {"action": action, "payload": payload.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        await lock_references(self.session)
        receipt = await self.session.get(DraftReceipt, payload.operation_id)
        if receipt:
            if receipt.actor_id != actor.id or receipt.digest != digest:
                raise AuthFailure(409, 40950, "操作标识已用于另一项修改，请重新操作")
            return TranslationCatalog.model_validate(receipt.response)
        row = await self.session.get(ContentDraft, payload.id)
        if row is None:
            raise AuthFailure(404, 40450, "找不到这份草稿")
        group = await self.session.scalar(
            select(TranslationGroup)
            .where(TranslationGroup.id == row.translation_group_id)
            .with_for_update()
        )
        if group is None or group.revision != payload.expected_revision:
            raise AuthFailure(409, 40990, "语言关联已变化，请刷新后重试")
        members = list(
            await self.session.scalars(
                select(ContentDraft)
                .where(ContentDraft.translation_group_id == group.id)
                .order_by(ContentDraft.id)
                .with_for_update()
            )
        )
        source = next(item for item in members if item.id == group.source_id)
        now = datetime.now(UTC)
        publication = ContentPublication(self.session, self.config)
        changes: list[CacheChange] = []
        refresh_public = False
        if action == "create" and isinstance(payload, TranslationCreate):
            await require_language(self.session, payload.language)
            if any(item.language == payload.language for item in members):
                raise AuthFailure(409, 40990, "该语言版本已存在，请直接打开或恢复")
            if source.deleted_at:
                raise AuthFailure(409, 40952, "请先恢复原文，再创建译文")
            await require_unlocked(self.session, source.id)
            published = (
                await self.session.get(Content, source.id) if source.published_revision else None
            )
            settings = EditorSettings.model_validate(
                published.editor_settings if published else source.editor_settings
            )
            identity = uuid4()
            settings.path = await initial_path(
                self.session,
                source.title,
                identity,
                language=payload.language,
                type_id=source.type_id,
            )
            settings.seo_title, settings.seo_description = "", ""
            settings.seo_canonical_path = settings.seo_share_title = (
                settings.seo_share_description
            ) = ""
            settings.seo_image_alt = ""
            settings.opens_at = None
            mode = (
                ("components" if published.component_document is not None else "blocks")
                if published
                else source.edit_mode
            )
            source_revision = (
                await self.session.get(DraftRevision, (source.id, source.published_revision))
                if published and source.published_revision and payload.copy_source
                else None
            )
            component_document = None
            if mode == "components":
                component_document = ComponentDocument(version=1, nodes=[]).model_dump(mode="json")
                if payload.copy_source:
                    original_document = (
                        source_revision.snapshot.get("component_document")
                        if source_revision is not None
                        else source.component_document
                    )
                    if original_document is not None:
                        component_document = original_document
            blocks = (
                source_revision.snapshot["body"]
                if source_revision is not None
                else (published.content if published else source.body) or source.body
            )
            body = (
                [{**item, "id": str(uuid4())} for item in blocks]
                if payload.copy_source
                else [{"id": str(uuid4()), "type": "markdown", "content": ""}]
            )
            await editor_media(
                self.session, self.config, json.dumps(component_document or body), settings
            )
            created = ContentDraft(
                id=identity,
                language=payload.language,
                translation_group_id=group.id,
                translated_from_revision=source.published_revision or source.revision,
                type_id=source.type_id,
                title=published.title if published else source.title,
                excerpt="",
                body=body,
                edit_mode=mode,
                component_document=component_document,
                editor_settings=settings.model_dump(mode="json"),
                author=actor.display_name,
                created_by=actor.id,
                revision=1,
                is_demo=False,
                created_at=now,
                updated_at=now,
            )
            self.session.add(created)
            self.session.add(
                DraftRevision(
                    draft_id=identity,
                    revision=1,
                    actor_id=actor.id,
                    action="create",
                    snapshot={
                        **summary(created).model_dump(mode="json"),
                        "body": body,
                        "component_document": component_document,
                        "settings": settings.model_dump(mode="json"),
                        "has_password": False,
                    },
                    created_at=now,
                    parents=[],
                )
            )
        elif action == "link" and isinstance(payload, TranslationLink):
            await require_unlocked(self.session, source.id)
            target = await self.session.get(ContentDraft, payload.target_id, with_for_update=True)
            if target is None or target.deleted_at or target.type_id != group.type_id:
                raise AuthFailure(422, 42290, "请选择同类型且未回收的内容")
            if any(item.language == target.language for item in members):
                raise AuthFailure(409, 40990, "该语言版本已存在，请直接打开或恢复")
            await require_unlocked(self.session, target.id)
            old = await self.session.get(
                TranslationGroup, target.translation_group_id, with_for_update=True
            )
            count = await self.session.scalar(
                select(ContentDraft.id)
                .where(
                    ContentDraft.translation_group_id == target.translation_group_id,
                    ContentDraft.id != target.id,
                )
                .limit(1)
            )
            if old is None or count is not None:
                raise AuthFailure(409, 40990, "目标已有其他译文，请先解除原有关联")
            target.translation_group_id = group.id
            target.translated_from_revision = source.published_revision or source.revision
            public_target = await self.session.get(Content, target.id)
            if public_target:
                public_target.translation_group_id = group.id
                public_target.public_generation += 1
                refresh_public = True
            await self.session.delete(old)
        elif action == "unlink":
            if row.id == source.id:
                raise AuthFailure(409, 40990, "请从译文入口解除关联，原文保留当前组")
            await require_unlocked(self.session, row.id)
            await require_unlocked(self.session, source.id)
            new_group = TranslationGroup(
                id=uuid4(), source_id=row.id, type_id=row.type_id, revision=1
            )
            self.session.add(new_group)
            row.translation_group_id = new_group.id
            row.translated_from_revision = None
            public_row = await self.session.get(Content, row.id)
            if public_row:
                public_row.translation_group_id = new_group.id
                public_row.public_generation += 1
                refresh_public = True
        elif action in {"trash", "restore"} and isinstance(payload, TranslationGroupMutation):
            if payload.members != {item.id: item.revision for item in members}:
                raise AuthFailure(409, 40951, "内容已在其他地方修改，请保留输入并重新读取后再保存")
            for item in members:
                await require_unlocked(self.session, item.id)
            for item in members:
                if (action == "trash") == bool(item.deleted_at):
                    continue
                item.deleted_at = now if action == "trash" else None
                item.updated_at, item.revision = now, item.revision + 1
                if action == "trash":
                    from app.modules.content.scheduled_publication import clear_schedule

                    clear_schedule(item)
                    change = await publication.withdraw(item)
                    if change is not None:
                        changes.append(change)
                self.session.add(
                    DraftRevision(
                        draft_id=item.id,
                        revision=item.revision,
                        actor_id=actor.id,
                        action=action,
                        snapshot={
                            **summary(item).model_dump(mode="json"),
                            "body": item.body,
                            "component_document": item.component_document,
                            "settings": item.editor_settings,
                            "has_password": bool(item.access_password_hash),
                        },
                        created_at=now,
                        parents=[item.revision - 1],
                    )
                )
        elif action == "review":
            await require_unlocked(self.session, row.id)
            row.translated_from_revision = source.published_revision or source.revision
        else:
            raise AuthFailure(422, 42290, "不支持此语言操作")
        group.revision += 1
        await self.session.flush()
        if refresh_public or changes:
            await publication.stage(*(changes or [CacheChange("translations_changed")]))
        result = await self.read(source.id)
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource=f"translations:{group.id}",
                revision=group.revision,
                changes={
                    "action": action,
                    "id": str(payload.id),
                    "members": [str(item.id) for item in result.members],
                },
            )
        )
        self.session.add(
            DraftReceipt(
                id=payload.operation_id,
                actor_id=actor.id,
                digest=digest,
                draft_id=payload.id,
                response=result.model_dump(mode="json"),
                created_at=now,
            )
        )
        await self.session.commit()
        return result
