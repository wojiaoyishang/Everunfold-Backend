"""Trash, safe shared-file removal, and frozen restartable hash-group purges."""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references, media_operation
from app.modules.media.models import MediaAsset, MediaRemovalReceipt
from app.modules.media.removal_plan import make_plan, pending_receipt, receipt_preview
from app.modules.media.schemas import (
    MediaLookup,
    MediaRemoval,
    MediaRemovalIntent,
    MediaRemovalPreview,
    MediaRemove,
)
from app.modules.media.service import MediaService
from app.modules.settings.models import SettingsAudit


class MediaRemovalService(MediaService):
    async def initial(self, reference: str) -> MediaAsset:
        asset = await self.session.get(MediaAsset, uuid.UUID(reference[6:]))
        if asset is None or asset.status != "ready":
            raise AuthFailure(404, 40430, "资源不存在")
        return asset

    async def preview(self, payload: MediaRemovalIntent) -> MediaRemovalPreview:
        initial = await self.initial(payload.id)
        async with media_operation(self.session, f"media-hash:{initial.sha256}") as work:
            await lock_references(work)
            pending = await pending_receipt(work, initial.sha256)
            if pending:
                return receipt_preview(pending)
            asset = await work.get(MediaAsset, initial.id, with_for_update=True)
            assert asset is not None
            return (await make_plan(work, asset, payload)).preview

    async def remove(self, payload: MediaRemove, actor: uuid.UUID) -> MediaRemoval:
        initial = await self.initial(payload.id)
        operation_id = uuid.UUID(payload.operation_id)
        async with media_operation(
            self.session, f"media-removal:{operation_id}", f"media-hash:{initial.sha256}"
        ) as work:
            receipt = await work.get(MediaRemovalReceipt, operation_id)
            if receipt:
                if (
                    receipt.asset_id != initial.id
                    or receipt.token != payload.expected_plan
                    or receipt.delete_original != payload.delete_original
                    or receipt.safe_delete != payload.safe_delete
                ):
                    raise AuthFailure(409, 40930, "该操作标识已用于其他删除")
                return await self.finish(work, receipt, actor)
            if await pending_receipt(work, initial.sha256):
                raise AuthFailure(409, 40939, "同一文件尚有未完成删除，请重新确认后重试")
            await lock_references(work)
            asset = await work.get(MediaAsset, initial.id, with_for_update=True)
            assert asset is not None
            intent = MediaRemovalIntent(
                **payload.model_dump(exclude={"operation_id", "expected_plan"})
            )
            plan = await make_plan(work, asset, intent)
            if plan.preview.token != payload.expected_plan:
                raise AuthFailure(409, 40939, "删除范围已变化，请重新确认")
            now = datetime.now(UTC)
            receipt = MediaRemovalReceipt(
                id=operation_id,
                actor_id=actor,
                asset_id=asset.id,
                sha256=asset.sha256,
                delete_original=payload.delete_original,
                safe_delete=payload.safe_delete,
                token=plan.preview.token,
                record_ids=[str(row.id) for row in plan.records],
                file_ids=[str(row.id) for row in plan.files],
                file_retained=plan.preview.file_retained,
                completed_at=None if plan.files else now,
            )
            work.add(receipt)
            affected = {row.id: row for row in plan.records + plan.files}
            for row in affected.values():
                row.deleted_at = row.deleted_at or now
                if payload.delete_original:
                    row.removed_at = row.removed_at or now
                    row.purge_pending = bool(plan.files)
                row.revision += 1
                work.add(
                    SettingsAudit(
                        user_id=actor,
                        resource=f"media:{row.id}",
                        revision=row.revision,
                        changes={
                            "operation": "purge-requested"
                            if plan.files
                            else "record-removed"
                            if payload.delete_original
                            else "trash",
                            "operation_id": payload.operation_id,
                            "safe_delete": payload.safe_delete,
                            "file_retained": plan.preview.file_retained,
                        },
                    )
                )
            # Every affected ID becomes unavailable in one commit before any filesystem deletion.
            await work.commit()
            return await self.finish(work, receipt, actor)

    async def finish(
        self, work: AsyncSession, receipt: MediaRemovalReceipt, actor: uuid.UUID
    ) -> MediaRemoval:
        if receipt.completed_at is None:
            files = list(
                await work.scalars(
                    select(MediaAsset).where(
                        MediaAsset.id.in_([uuid.UUID(key) for key in receipt.file_ids])
                    )
                )
            )
            if len(files) != len(receipt.file_ids):
                raise AuthFailure(503, 50331, "删除记录不完整，请稍后重试")
            await work.commit()
            try:
                for asset in files:
                    await asyncio.to_thread(self.storage.remove, asset.object_key, asset.sha256)
            except OSError:
                raise AuthFailure(
                    503, 50331, "文件已停止公开，原文件暂未全部删除，可以重试"
                ) from None
            now = datetime.now(UTC)
            for asset in files:
                asset.purged_at = now
                asset.purge_pending = False
                asset.revision += 1
                work.add(
                    SettingsAudit(
                        user_id=actor,
                        resource=f"media:{asset.id}",
                        revision=asset.revision,
                        changes={"operation": "purged", "operation_id": str(receipt.id)},
                    )
                )
            receipt.completed_at = now
            await work.commit()
        return MediaRemoval(
            id=f"media:{receipt.asset_id}",
            deleted=True,
            purged=bool(receipt.file_ids),
            purge_pending=False,
            records_removed=len(receipt.record_ids),
            files_deleted=len(receipt.file_ids),
            file_retained=receipt.file_retained,
        )

    async def restore(self, payload: MediaLookup, actor: uuid.UUID) -> MediaRemoval:
        initial = await self.initial(payload.id)
        async with media_operation(self.session, f"media-hash:{initial.sha256}") as work:
            asset = await work.get(MediaAsset, initial.id, with_for_update=True)
            assert asset is not None
            if asset.removed_at or asset.purged_at or asset.purge_pending:
                raise AuthFailure(409, 40938, "记录已永久删除或正在删除，无法恢复")
            if asset.deleted_at:
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
