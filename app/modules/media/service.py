"""Media identity, validation and transactional registration stay outside storage plugins."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.media.media_validation import validate_media
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import (
    IconAsset,
    IconCatalog,
    IconQuery,
    IconUpload,
    MediaCatalog,
    MediaItem,
    MediaQuery,
    MediaUpload,
)
from app.modules.media.storage import LocalObjectStorage, ObjectStorage
from app.modules.media.validation import validate_icon
from app.modules.settings.models import SettingsAudit


class MediaService:
    def __init__(
        self, session: AsyncSession, settings: Settings, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage or LocalObjectStorage(settings.uploads_directory)

    def dto(self, asset: MediaAsset) -> IconAsset:
        return IconAsset(
            id=f"media:{asset.id}",
            label=asset.filename,
            url=self.settings.media_base_url + "/" + asset.object_key,
            mime=asset.mime,
            size=asset.size,
            created_at=asset.created_at,
        )

    async def catalog(self, query: IconQuery) -> IconCatalog:
        where = [
            MediaAsset.status == "ready",
            MediaAsset.deleted_at.is_(None),
            MediaAsset.purpose == "icon",
        ]
        if query.query.strip():
            where.append(MediaAsset.filename.icontains(query.query.strip(), autoescape=True))
        total = (
            await self.session.scalar(select(func.count()).select_from(MediaAsset).where(*where))
            or 0
        )
        assets = await self.session.scalars(
            select(MediaAsset)
            .where(*where)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .offset((query.page - 1) * 60)
            .limit(60)
        )
        return IconCatalog(
            items=[self.dto(asset) for asset in assets], page=query.page, total=total
        )

    async def upload(self, payload: IconUpload, actor: uuid.UUID) -> IconAsset:
        data, mime, extension = await asyncio.to_thread(validate_icon, payload.data_base64)
        return await self.publish(
            data, mime, extension, payload.filename, payload.upload_id, actor, "icon"
        )

    async def upload_media(self, payload: MediaUpload, data: bytes, actor: uuid.UUID) -> MediaItem:
        mime, extension = await asyncio.to_thread(validate_media, data)
        asset = await self.publish(
            data, mime, extension, payload.filename, payload.upload_id, actor, "library"
        )
        return MediaItem(
            **asset.model_dump(), kind="video" if mime.startswith("video/") else "image"
        )

    async def publish(
        self,
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
        media_id = uuid.uuid5(actor, upload_id)
        now = datetime.now(UTC)
        key = f"{now:%Y/%m}/{media_id}/original.{extension}"
        name = filename.replace("\\", "/").split("/")[-1]
        if not name.strip() or any(ord(char) < 32 for char in name):
            raise AuthFailure(422, 42230, "文件名称无效")
        await self.session.execute(
            insert(MediaAsset)
            .values(
                id=media_id,
                uploader_id=actor,
                filename=name,
                mime=mime,
                size=len(data),
                sha256=digest,
                object_key=key,
                status="staging",
                purpose=purpose,
                storage_id="local",
                source_id=source_id,
                edit_recipe=recipe,
                **(metadata or {}),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        asset = await self.session.get(MediaAsset, media_id)
        assert asset is not None
        if (
            asset.sha256 != digest
            or asset.filename != name
            or asset.purpose != purpose
            or asset.source_id != source_id
            or asset.edit_recipe != recipe
            or asset.deleted_at is not None
        ):
            raise AuthFailure(409, 40930, "该上传标识已经用于其他文件")
        # Persist the resumable upload identity before the external filesystem operation.
        await self.session.commit()
        await asyncio.to_thread(self.storage.publish, asset.object_key, data, digest)
        asset = await self.session.scalar(
            select(MediaAsset)
            .where(MediaAsset.id == media_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert asset is not None
        if asset.status != "ready":
            asset.status = "ready"
            self.session.add(
                SettingsAudit(
                    user_id=actor,
                    resource=f"media:{media_id}",
                    revision=1,
                    changes={
                        "operation": "edit" if source_id else "upload",
                        "sha256": digest,
                        "size": len(data),
                        "source_id": str(source_id) if source_id else None,
                    },
                )
            )
        await self.session.commit()
        return self.dto(asset)

    async def resolve_icons(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        assets = await self.session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_([uuid.UUID(key[6:]) for key in ids]),
                MediaAsset.status == "ready",
                MediaAsset.deleted_at.is_(None),
                MediaAsset.purpose == "icon",
            )
        )
        result = {self.dto(asset).id: self.dto(asset).url for asset in assets}
        if len(result) != len(ids):
            raise AuthFailure(422, 42231, "部分图标已不可用，请重新选择")
        return result

    async def catalog_media(self, query: MediaQuery) -> MediaCatalog:
        where = [MediaAsset.status == "ready", MediaAsset.deleted_at.is_(None)]
        if query.kind != "all":
            where.append(MediaAsset.mime.startswith(query.kind + "/"))
        if query.query.strip():
            term = query.query.strip()
            where.append(
                or_(
                    MediaAsset.filename.icontains(term, autoescape=True),
                    MediaAsset.title.icontains(term, autoescape=True),
                )
            )
        total = (
            await self.session.scalar(select(func.count()).select_from(MediaAsset).where(*where))
            or 0
        )
        assets = await self.session.scalars(
            select(MediaAsset)
            .where(*where)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .offset((query.page - 1) * 60)
            .limit(60)
        )
        return MediaCatalog(
            items=[
                MediaItem(
                    **self.dto(asset).model_dump(),
                    kind="video" if asset.mime.startswith("video/") else "image",
                    title=asset.title,
                )
                for asset in assets
            ],
            page=query.page,
            total=total,
        )

    async def resolve_resources(self, references: dict[str, str]) -> dict[str, str]:
        if not references:
            return {}
        assets = await self.session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_([uuid.UUID(key[6:]) for key in references]),
                MediaAsset.status == "ready",
                MediaAsset.deleted_at.is_(None),
            )
        )
        result = {}
        for asset in assets:
            key = f"media:{asset.id}"
            if not asset.mime.startswith(references[key] + "/"):
                raise AuthFailure(422, 42234, "资源类型不匹配，请重新选择")
            result[key] = self.dto(asset).url
        if len(result) != len(references):
            raise AuthFailure(422, 42234, "部分媒体资源不可用，请重新选择")
        return result
