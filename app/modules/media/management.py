"""Attachment details, optimistic metadata updates and immutable edited copies."""

import asyncio
import uuid
from pathlib import PurePosixPath

from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.media.image_processing import BITMAP_MIMES, inspect_bitmap, transform_bitmap
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import MediaDetails, MediaEdit, MediaMetadata, MediaUpdate
from app.modules.media.service import MediaService
from app.modules.media.upload_validation import media_kind
from app.modules.settings.models import SettingsAudit


class MediaManagement(MediaService):
    async def asset(
        self, reference: str, *, lock: bool = False, include_deleted: bool = False
    ) -> MediaAsset:
        statement = select(MediaAsset).where(
            MediaAsset.id == uuid.UUID(reference[6:]),
            MediaAsset.status == "ready",
            MediaAsset.purged_at.is_(None),
        )
        if not include_deleted:
            statement = statement.where(MediaAsset.deleted_at.is_(None))
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        asset = await self.session.scalar(statement)
        if asset is None:
            raise AuthFailure(404, 40430, "资源不存在")
        return asset

    async def details(self, reference: str) -> MediaDetails:
        asset = await self.asset(reference, include_deleted=True)
        width, height = (None, None)
        if asset.mime in BITMAP_MIMES and not asset.deleted_at:
            width, height = await asyncio.to_thread(
                inspect_bitmap, self.storage.path(asset.object_key)
            )
        uploader = await self.session.scalar(
            select(User.display_name).where(User.id == asset.uploader_id)
        )
        return MediaDetails(
            **self.dto(asset).model_dump(),
            kind=media_kind(asset.mime),
            deleted=asset.deleted_at is not None,
            purge_pending=asset.purge_pending,
            title=asset.title,
            metadata=MediaMetadata.model_validate(asset, from_attributes=True),
            revision=asset.revision,
            width=width,
            height=height,
            uploader=uploader or "未知用户",
            source_id=f"media:{asset.source_id}" if asset.source_id else None,
            editable=width is not None and height is not None,
        )

    async def update(self, payload: MediaUpdate, actor: uuid.UUID) -> MediaDetails:
        asset = await self.asset(payload.id, lock=True)
        values = payload.metadata.model_dump()
        changed = {key: value for key, value in values.items() if getattr(asset, key) != value}
        if changed and asset.revision != payload.expected_revision:
            raise AuthFailure(409, 40931, "资料已被其他窗口修改，请重新读取后再保存")
        if changed:
            for key, value in changed.items():
                setattr(asset, key, value)
            asset.revision += 1
            self.session.add(
                SettingsAudit(
                    user_id=actor,
                    resource=payload.id,
                    revision=asset.revision,
                    changes={"operation": "metadata", "fields": list(changed)},
                )
            )
        await self.session.commit()
        return await self.details(payload.id)

    async def edit(self, payload: MediaEdit, actor: uuid.UUID) -> MediaDetails:
        # Persisted recipe identity allows retries across replicas after lost responses.
        key = uuid.uuid5(actor, payload.edit_id)
        existing = await self.session.get(MediaAsset, key)
        recipe = payload.recipe.model_dump(mode="json")
        source = await self.asset(payload.id)
        if existing is not None:
            if (
                existing.source_id != source.id
                or existing.edit_recipe != recipe
                or existing.deleted_at
            ):
                raise AuthFailure(409, 40930, "该编辑标识已用于其他操作")
            if existing.status == "ready":
                return await self.details(f"media:{key}")
        if source.mime not in BITMAP_MIMES:
            raise AuthFailure(415, 41531, "目前支持编辑 PNG、JPEG 和 WebP 静态图片")
        path = self.storage.path(source.object_key)
        metadata = MediaMetadata.model_validate(source, from_attributes=True).model_dump()
        source_id = source.id
        filename = PurePosixPath(source.filename).stem[:140] + "-edited"
        # Release the read transaction during CPU/filesystem work.
        await self.session.commit()
        data, mime, extension = await asyncio.to_thread(transform_bitmap, path, payload.recipe)
        uploaded = await self.publish(
            data,
            mime,
            extension,
            filename + "." + extension,
            payload.edit_id,
            actor,
            "library",
            source_id=source_id,
            recipe=recipe,
            metadata=metadata,
        )
        return await self.details(uploaded.id)
