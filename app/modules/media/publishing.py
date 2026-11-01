"""Durable upload receipts and hash reuse, serialized across API processes."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references, media_operation
from app.modules.media.models import MediaAsset, MediaUploadReceipt
from app.modules.media.schemas import IconAsset

if TYPE_CHECKING:
    from app.modules.media.service import MediaService
from app.modules.settings.models import SettingsAudit


async def publish(
    service: "MediaService",
    data: bytes,
    mime: str,
    extension: str,
    filename: str,
    upload_id: str,
    actor: uuid.UUID,
    purpose: str,
    *,
    source_id: uuid.UUID | None = None,
    recipe: dict[str, Any] | None = None,
    metadata: dict[str, str] | None = None,
) -> IconAsset:
    digest = hashlib.sha256(data).hexdigest()
    identity = uuid.uuid5(actor, upload_id)
    name = filename.replace("\\", "/").split("/")[-1]
    if not name.strip() or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise AuthFailure(422, 42230, "文件名称无效")
    signature = hashlib.sha256(
        json.dumps([digest, name, purpose, str(source_id), recipe], sort_keys=True).encode()
    ).hexdigest()
    async with media_operation(
        service.session, f"media-request:{identity}", f"media-hash:{digest}"
    ) as work:
        receipt = await work.get(MediaUploadReceipt, identity)
        reused = False
        if receipt:
            if receipt.signature != signature:
                raise AuthFailure(409, 40930, "该上传标识已经用于其他文件")
            asset = await work.get(MediaAsset, receipt.asset_id)
            reused = receipt.reused
        else:
            # Compatibility with uploads made before receipts were introduced.
            asset = await work.get(MediaAsset, identity)
            if asset and (
                asset.sha256 != digest
                or asset.filename != name
                or asset.purpose != purpose
                or asset.source_id != source_id
                or asset.edit_recipe != recipe
            ):
                raise AuthFailure(409, 40930, "该上传标识已经用于其他文件")
            if asset is None and source_id is None:
                asset = await work.scalar(
                    select(MediaAsset)
                    .where(MediaAsset.sha256 == digest, MediaAsset.deleted_at.is_(None))
                    .order_by(MediaAsset.status.desc(), MediaAsset.created_at, MediaAsset.id)
                    .limit(1)
                )
                reused = asset is not None
            if asset is None:
                if source_id:
                    await lock_references(work)
                    source = await work.get(MediaAsset, source_id)
                    if source is None or source.deleted_at is not None:
                        raise AuthFailure(409, 40935, "原文件已不可用，请重新选择")
                now = datetime.now(UTC)
                asset = MediaAsset(
                    id=identity,
                    uploader_id=actor,
                    filename=name,
                    mime=mime,
                    size=len(data),
                    sha256=digest,
                    object_key=f"{now:%Y/%m}/{identity}/original.{extension}",
                    status="staging",
                    purpose=purpose,
                    storage_id="local",
                    source_id=source_id,
                    edit_recipe=recipe,
                    icon_enabled=purpose == "icon",
                    **(metadata or {}),
                )
                work.add(asset)
                await work.flush()
            work.add(
                MediaUploadReceipt(
                    id=identity, signature=signature, asset_id=asset.id, reused=reused
                )
            )
        assert asset is not None
        if asset.deleted_at is not None:
            raise AuthFailure(410, 41030, "这个上传对应的文件已经删除，请重新选择文件")
        promoted = purpose == "icon" and not asset.icon_enabled
        if promoted:
            asset.icon_enabled = True
            work.add(
                SettingsAudit(
                    user_id=actor,
                    resource=f"media:{asset.id}",
                    revision=asset.revision,
                    changes={"operation": "enable-icon"},
                )
            )
        # The upload identity survives process failure; storage I/O has no open transaction.
        await work.commit()
        if asset.status == "staging":
            await asyncio.to_thread(service.storage.publish, asset.object_key, data, digest)
            asset.status = "ready"
            work.add(
                SettingsAudit(
                    user_id=actor,
                    resource=f"media:{asset.id}",
                    revision=asset.revision,
                    changes={
                        "operation": "edit" if source_id else "upload",
                        "sha256": digest,
                        "size": len(data),
                        "source_id": str(source_id) if source_id else None,
                    },
                )
            )
            await work.commit()
        elif not await asyncio.to_thread(service.storage.path(asset.object_key).is_file):
            raise AuthFailure(503, 50330, "原文件暂时不可用，请稍后重试")
        return service.dto(asset).model_copy(update={"reused": reused})
