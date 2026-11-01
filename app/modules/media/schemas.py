"""Backend-published contracts for the shared icon browser."""

from datetime import datetime

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
