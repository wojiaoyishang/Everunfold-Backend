"""Backend-published contracts for the shared icon browser."""

from datetime import datetime
from typing import Annotated, Literal

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
    title: str = ""


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


MediaReference = Annotated[
    str, Field(pattern=r"^media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
]


class MediaLookup(PublicModel):
    id: MediaReference


class MediaMetadata(PublicModel):
    title: str = Field(default="", max_length=160)
    alt_text: str = Field(default="", max_length=1000)
    caption: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=10000)


class MediaDetails(MediaItem):
    metadata: MediaMetadata
    revision: int
    width: int | None
    height: int | None
    uploader: str
    source_id: str | None
    editable: bool


class MediaUpdate(MediaLookup):
    expected_revision: int = Field(ge=1)
    metadata: MediaMetadata


class CropRect(PublicModel):
    x: int = Field(ge=0, le=8192, strict=True)
    y: int = Field(ge=0, le=8192, strict=True)
    width: int = Field(ge=1, le=8192, strict=True)
    height: int = Field(ge=1, le=8192, strict=True)


class ImageRecipe(PublicModel):
    # EXIF orientation, then clockwise rotation, then flips, crop, proportional resize.
    rotation: Literal[0, 90, 180, 270] = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    crop: CropRect | None = None
    output_width: int | None = Field(default=None, ge=1, le=8192, strict=True)


class MediaEdit(MediaLookup):
    edit_id: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    recipe: ImageRecipe
