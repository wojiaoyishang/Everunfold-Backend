"""Resolve deletion scope under the hash lock and the reference-writer guard."""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.media.models import MediaAsset, MediaRemovalReceipt
from app.modules.media.references import require_unreferenced
from app.modules.media.schemas import MediaRemovalIntent, MediaRemovalPreview


@dataclass
class RemovalPlan:
    records: list[MediaAsset]
    files: list[MediaAsset]
    preview: MediaRemovalPreview


def receipt_preview(receipt: MediaRemovalReceipt) -> MediaRemovalPreview:
    return MediaRemovalPreview(
        id=f"media:{receipt.asset_id}",
        token=receipt.token,
        record_count=len(receipt.record_ids),
        file_count=len(receipt.file_ids),
        file_retained=receipt.file_retained,
        delete_original=receipt.delete_original,
        safe_delete=receipt.safe_delete,
        resume_operation_id=str(receipt.id),
    )


async def pending_receipt(session: AsyncSession, digest: str) -> MediaRemovalReceipt | None:
    return await session.scalar(
        select(MediaRemovalReceipt).where(
            MediaRemovalReceipt.sha256 == digest, MediaRemovalReceipt.completed_at.is_(None)
        )
    )


async def make_plan(
    session: AsyncSession, asset: MediaAsset, intent: MediaRemovalIntent
) -> RemovalPlan:
    if asset.removed_at or asset.purged_at:
        raise AuthFailure(410, 41030, "这条文件记录已经删除")
    if asset.revision != intent.expected_revision:
        raise AuthFailure(409, 40931, "文件资料已变更，请重新读取后再删除")
    group = list(
        await session.scalars(
            select(MediaAsset)
            .where(MediaAsset.sha256 == asset.sha256, MediaAsset.purged_at.is_(None))
            .order_by(MediaAsset.id)
            .with_for_update()
        )
    )
    live = [row for row in group if row.removed_at is None]
    retained = intent.delete_original and intent.safe_delete and len(live) > 1
    records = live if intent.delete_original and not intent.safe_delete else [asset]
    files = group if intent.delete_original and not retained else []
    for row in {row.id: row for row in records + files}.values():
        await require_unreferenced(
            session,
            row,
            history=intent.delete_original,
            removing_ids={item.id for item in records + files} if intent.delete_original else None,
        )
    token = hashlib.sha256(
        json.dumps(
            [
                intent.model_dump(mode="json"),
                [
                    (
                        str(row.id),
                        row.revision,
                        str(row.deleted_at),
                        str(row.removed_at),
                        row.purge_pending,
                    )
                    for row in group
                ],
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return RemovalPlan(
        records,
        files,
        MediaRemovalPreview(
            id=intent.id,
            token=token,
            record_count=len(records),
            file_count=len(files),
            file_retained=retained,
            delete_original=intent.delete_original,
            safe_delete=intent.safe_delete,
        ),
    )
