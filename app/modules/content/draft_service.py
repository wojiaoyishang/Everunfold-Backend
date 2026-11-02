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
from app.modules.auth.models import User
from app.modules.auth.security import hash_password
from app.modules.auth.service import AuthFailure
from app.modules.content.content_paths import initial_path
from app.modules.content.draft_models import ContentDraft, DraftReceipt, DraftRevision
from app.modules.content.draft_repository import DraftRepository, detail
from app.modules.content.draft_schemas import DraftCreate, DraftDetail, DraftMutation, DraftSave
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.publication import ContentPublication
from app.modules.content.type_registry import ContentTypeDefinition, content_types
from app.modules.media.locking import lock_references
from app.modules.media.models import MediaAsset

Action = Literal["create", "save", "trash", "restore", "publish", "withdraw"]


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

    async def read(self, draft_id: UUID) -> DraftDetail:
        row = await self.repo.find(draft_id)
        if row is None:
            raise AuthFailure(404, 40450, "找不到这份草稿")
        result = detail(row)
        result.media_urls = await editor_media(
            self.session, self.config, json.dumps(row.body), result.settings, strict=False
        )
        return result

    async def mutate(
        self,
        action: Action,
        payload: DraftCreate | DraftSave | DraftMutation,
        actor: User,
        *,
        demo: bool = False,
    ) -> DraftDetail:
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
        digest = hashlib.sha256(
            json.dumps(
                {"action": action, "payload": serialized, "demo": demo},
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
            row = ContentDraft(
                id=uuid4(),
                type_id=payload.type_id,
                title="",
                excerpt="",
                body=[],
                editor_settings={},
                author=actor.display_name,
                created_by=actor.id,
                revision=1,
                is_demo=demo,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            existing = await self.repo.find(payload.id, lock=True)
            if existing is None:
                raise AuthFailure(404, 40450, "找不到这份草稿")
            row = existing
            if row.revision != payload.expected_revision:
                raise AuthFailure(409, 40951, "内容已在其他地方修改，请保留输入并重新读取后再保存")
            row.revision += 1
            row.updated_at = now
        definition = require_type(row.type_id)
        if isinstance(payload, DraftCreate | DraftSave):
            if row.deleted_at:
                raise AuthFailure(409, 40952, "请先从回收站恢复，再编辑")
            fields = payload.fields
            if definition.title_required and not fields.title.strip():
                raise AuthFailure(422, 42251, "请填写标题")
            if not definition.supports_excerpt and fields.excerpt:
                raise AuthFailure(422, 42252, "此内容类型不使用独立摘要")
            await self._check_media(
                json.dumps([block.model_dump(mode="json") for block in fields.body])
            )
            settings = fields.settings or EditorSettings.model_validate(row.editor_settings or {})
            if not settings.path:
                settings.path = row.public_path or await initial_path(
                    self.session, fields.title, row.id
                )
            if fields.access_password:
                row.access_password_hash = await to_thread.run_sync(
                    hash_password, fields.access_password
                )
            if settings.visibility == "password" and not row.access_password_hash:
                raise AuthFailure(422, 42259, "请先设置文章密码")
            if settings.visibility == "public" and settings.opens_at is not None:
                raise AuthFailure(422, 42259, "定时开放需要先选择密码保护")
            await editor_media(
                self.session,
                self.config,
                json.dumps([block.model_dump(mode="json") for block in fields.body]),
                settings,
            )
            row.title, row.excerpt = fields.title.strip(), fields.excerpt.strip()
            row.body = [block.model_dump(mode="json") for block in fields.body]
            row.editor_settings = settings.model_dump(mode="json")
        elif action == "trash":
            if row.deleted_at:
                raise AuthFailure(409, 40952, "内容已在回收站中")
            row.deleted_at = now
        elif action == "restore":
            if not row.deleted_at:
                raise AuthFailure(409, 40952, "内容已经恢复")
            row.deleted_at = None
        if action in {"publish", "withdraw"} or (action == "trash" and row.published_revision):
            if self.config is None:
                raise RuntimeError("Publication requires configured delivery targets")
            publication = ContentPublication(self.session, self.config)
            if action == "publish":
                await publication.publish(row)
            else:
                await publication.withdraw(row)
            await publication.stage()
        result = detail(row)
        result.media_urls = await editor_media(
            self.session, self.config, json.dumps(row.body), result.settings, strict=False
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
            await self.session.commit()
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
