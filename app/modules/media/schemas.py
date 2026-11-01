"""Backend-published contracts for the shared icon browser."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.core.schemas import PublicModel


class IconAsset(PublicModel):
    id: str
    label: str
    url: str
    mime: str
    size: int
    created_at: datetime


class IconCatalog(PublicModel):
    items: list[IconAsset]
    page: int
    total: int
    page_size: int = 60


class IconQuery(PublicModel):
    query: str = Field(default="", max_length=160)
    page: int = Field(default=1, ge=1, le=10000)


class IconUpload(PublicModel):
    filename: str = Field(min_length=1, max_length=160)
    data_base64: str = Field(min_length=1, max_length=1398104)
    upload_id: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class MediaItem(IconAsset):
    kind: Literal["image", "video"]


class MediaCatalog(PublicModel):
    items: list[MediaItem]
    page: int
    total: int
    page_size: int = 60


class MediaQuery(IconQuery):
    kind: Literal["all", "image", "video"] = "all"


class MediaUpload(PublicModel):
    filename: str = Field(min_length=1, max_length=160)
    upload_id: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
