"""Recoverable trash and explicit, restartable original-file removal."""

import asyncio
import uuid
from datetime import UTC, datetime

from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references, media_operation
from app.modules.media.models import MediaAsset
from app.modules.media.references import require_unreferenced
from app.modules.media.schemas import MediaLookup, MediaRemoval, MediaRemove
from app.modules.media.service import MediaService
from app.modules.settings.models import SettingsAudit


class MediaRemovalService(MediaService):
    async def remove(self, payload: MediaRemove, actor: uuid.UUID) -> MediaRemoval:
        identity = uuid.UUID(payload.id[6:])
        initial = await self.session.get(MediaAsset, identity)
        if initial is None or initial.status != "ready":
            raise AuthFailure(404, 40430, "资源不存在")
        async with media_operation(self.session, f"media-hash:{initial.sha256}") as work:
            await lock_references(work)
            asset = await work.get(MediaAsset, identity, with_for_update=True)
            assert asset is not None
            if asset.purged_at:
                return MediaRemoval(id=payload.id, deleted=True, purged=True, purge_pending=False)
            if asset.deleted_at is None and asset.revision != payload.expected_revision:
                raise AuthFailure(409, 40931, "文件资料已变更，请重新读取后再删除")
            await require_unreferenced(work, asset, history=payload.delete_original)
            changed = asset.deleted_at is None or (
                payload.delete_original and not asset.purge_pending
            )
            if changed:
                asset.deleted_at = asset.deleted_at or datetime.now(UTC)
                asset.purge_pending = payload.delete_original
                asset.revision += 1
                work.add(
                    SettingsAudit(
                        user_id=actor,
                        resource=payload.id,
                        revision=asset.revision,
                        changes={
                            "operation": "purge-requested" if payload.delete_original else "trash"
                        },
                    )
                )
            await work.commit()
            if payload.delete_original:
                try:
                    await asyncio.to_thread(self.storage.remove, asset.object_key, asset.sha256)
                except OSError:
                    raise AuthFailure(
                        503, 50331, "文件已停止公开，原文件暂未删除，可以在回收站重试"
                    ) from None
                asset.purged_at = datetime.now(UTC)
                asset.purge_pending = False
                asset.revision += 1
                work.add(
                    SettingsAudit(
                        user_id=actor,
                        resource=payload.id,
                        revision=asset.revision,
                        changes={"operation": "purged"},
                    )
                )
                await work.commit()
            return MediaRemoval(
                id=payload.id,
                deleted=True,
                purged=asset.purged_at is not None,
                purge_pending=asset.purge_pending,
            )

    async def restore(self, payload: MediaLookup, actor: uuid.UUID) -> MediaRemoval:
        identity = uuid.UUID(payload.id[6:])
        initial = await self.session.get(MediaAsset, identity)
        if initial is None or initial.status != "ready":
            raise AuthFailure(404, 40430, "资源不存在")
        async with media_operation(self.session, f"media-hash:{initial.sha256}") as work:
            asset = await work.get(MediaAsset, identity, with_for_update=True)
            assert asset is not None
            if asset.purged_at or asset.purge_pending:
                raise AuthFailure(409, 40938, "原文件已删除或正在删除，无法恢复")
            if asset.deleted_at:
                # Release the read transaction while verifying the retained original.
                await work.commit()
                if not await asyncio.to_thread(
                    self.storage.matches, asset.object_key, asset.sha256
                ):
                    raise AuthFailure(409, 40938, "原文件缺失或已损坏，无法恢复")
                asset.deleted_at = None
                asset.revision += 1
                work.add(
                    SettingsAudit(
                        user_id=actor,
                        resource=payload.id,
                        revision=asset.revision,
                        changes={"operation": "restore"},
                    )
                )
                await work.commit()
            return MediaRemoval(id=payload.id, deleted=False, purged=False, purge_pending=False)
