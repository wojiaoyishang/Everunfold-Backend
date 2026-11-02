"""Trusted content-type registrations. Pages have a separate code-owned lifecycle."""

from collections.abc import Iterable
from types import MappingProxyType

from pydantic import ConfigDict, Field

from app.core.schemas import PublicModel


class ContentTypeDefinition(PublicModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    owner: str
    label: str
    description: str
    icon: str
    order: int
    title_required: bool
    supports_excerpt: bool
    editor: str
    panels: list[str] = Field(
        default_factory=lambda: ["publication", "cover", "taxonomy", "discussion", "seo"]
    )


class ContentTypeRegistry:
    def __init__(self, definitions: Iterable[ContentTypeDefinition]) -> None:
        items: dict[str, ContentTypeDefinition] = {}
        for definition in definitions:
            if definition.id in items:
                raise ValueError(f"Duplicate content type: {definition.id}")
            items[definition.id] = definition
        self._items = MappingProxyType(items)

    def all(self) -> tuple[ContentTypeDefinition, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.id)))

    def get(self, type_id: str) -> ContentTypeDefinition | None:
        return self._items.get(type_id)


content_types = ContentTypeRegistry(
    [
        ContentTypeDefinition(
            id="article",
            owner="content",
            label="文章",
            description="整理想法，写成一篇文章。",
            icon="book",
            order=10,
            title_required=True,
            supports_excerpt=True,
            editor="core.markdown",
        ),
        ContentTypeDefinition(
            id="shuoshuo",
            owner="content",
            label="说说",
            description="记录此刻，分享日常片段。",
            icon="leaf",
            order=20,
            title_required=False,
            supports_excerpt=False,
            editor="core.markdown",
        ),
    ]
)
