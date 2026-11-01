"""Public content release envelopes and private delivery status."""

from typing import Literal

from pydantic import Field

from app.core.schemas import PublicModel
from app.modules.content.schemas import PublicSnapshot
from app.modules.settings.schemas import DeliveryState


class ContentDelivery(PublicModel):
    protocol: Literal["content-1"] = "content-1"
    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    source_epoch: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    resource: Literal["public-content"] = "public-content"
    revision: int = Field(ge=1)
    value: PublicSnapshot


class ContentSyncState(PublicModel):
    source_epoch: str
    revision: int
    delivery: list[DeliveryState]


class ContentReceipt(PublicModel):
    node_id: str
    source_epoch: str
    resource: Literal["public-content"]
    revision: int
    digest: str
    durable: Literal[True]
