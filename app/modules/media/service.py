"""Media identity, validation and transactional registration stay outside storage plugins."""

import asyncio
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.media.models import MediaAsset
from app.modules.media.publishing import publish
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
from app.modules.media.upload_validation import media_kind, validate_upload
from app.modules.media.validation import validate_icon


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
            MediaAsset.icon_enabled.is_(True),
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
        mime, extension = await asyncio.to_thread(
            validate_upload, data, payload.filename, payload.purpose
        )
        asset = await self.publish(
            data, mime, extension, payload.filename, payload.upload_id, actor, payload.purpose
        )
        return MediaItem(**asset.model_dump(), kind=media_kind(mime))

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
        return await publish(
            self,
            data,
            mime,
            extension,
            filename,
            upload_id,
            actor,
            purpose,
            source_id=source_id,
            recipe=recipe,
            metadata=metadata,
        )

    async def resolve_icons(self, ids: set[str]) -> dict[str, str]:
        if not ids:
            return {}
        assets = await self.session.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_([uuid.UUID(key[6:]) for key in ids]),
                MediaAsset.status == "ready",
                MediaAsset.deleted_at.is_(None),
                MediaAsset.icon_enabled.is_(True),
            )
        )
        result = {self.dto(asset).id: self.dto(asset).url for asset in assets}
        if len(result) != len(ids):
            raise AuthFailure(422, 42231, "部分图标已不可用，请重新选择")
        return result

    async def catalog_media(self, query: MediaQuery) -> MediaCatalog:
        where = [
            MediaAsset.status == "ready",
            MediaAsset.purged_at.is_(None),
            or_(MediaAsset.removed_at.is_(None), MediaAsset.purge_pending.is_(True)),
            MediaAsset.deleted_at.is_not(None) if query.trash else MediaAsset.deleted_at.is_(None),
        ]
        if query.kind == "file":
            where.append(MediaAsset.mime == "application/octet-stream")
        elif query.kind != "all":
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
                    kind=media_kind(asset.mime),
                    deleted=asset.deleted_at is not None,
                    purge_pending=asset.purge_pending,
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
