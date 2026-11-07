"""Deploy-owned extension inventories and public activation preferences."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.core.schemas import PublicModel
from app.modules.pages.schemas import PageManifest

PluginId = r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$"
ThemeId = r"^[a-z][a-z0-9-]*$"


class ExtensionPreferences(PublicModel):
    theme: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9-]*)?$")
    disabled_plugins: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_plugins(self) -> Self:
        import re

        if len(set(self.disabled_plugins)) != len(self.disabled_plugins) or any(
            not re.fullmatch(PluginId, item) for item in self.disabled_plugins
        ):
            raise ValueError("invalid or duplicate plugin identifiers")
        self.disabled_plugins = sorted(self.disabled_plugins)
        return self


class InstalledTheme(PublicModel):
    id: str = Field(pattern=ThemeId)
    title: str = Field(min_length=1, max_length=80)
    version: str = Field(default="1.0.0", max_length=80)
    parent: str | None = Field(default=None, pattern=ThemeId)


class InstalledPlugin(PublicModel):
    id: str = Field(pattern=PluginId)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    build: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_ids: list[str] = Field(default_factory=list, max_length=100)
    components: list[str] = Field(default_factory=list, max_length=200)
    shortcodes: list[str] = Field(default_factory=list, max_length=100)


class ExtensionInventory(PublicModel):
    protocol: Literal["extensions-1"] = "extensions-1"
    themes: list[InstalledTheme] = Field(default_factory=list, max_length=100)
    plugins: list[InstalledPlugin] = Field(default_factory=list, max_length=100)
    pages: PageManifest

    @model_validator(mode="after")
    def unique_contributions(self) -> Self:
        if len({item.id for item in self.themes}) != len(self.themes) or len(
            {item.id for item in self.plugins}
        ) != len(self.plugins):
            raise ValueError("duplicate extension identifiers")
        page_ids = [page for plugin in self.plugins for page in plugin.page_ids]
        if len(set(page_ids)) != len(page_ids) or set(page_ids) != {
            page.id for page in self.pages.pages
        }:
            raise ValueError("page ownership does not match the manifest")
        by_id = {item.id: item for item in self.themes}
        for theme in self.themes:
            chain: set[str] = set()
            current: InstalledTheme | None = theme
            while current is not None:
                if current.id in chain or (current.parent and current.parent not in by_id):
                    raise ValueError("invalid theme ancestry")
                chain.add(current.id)
                current = by_id.get(current.parent or "")
        return self


class NodeExtensionInventory(PublicModel):
    node_id: str
    enabled: bool
    reported_at: datetime | None
    inventory: ExtensionInventory | None


class ExtensionCatalog(PublicModel):
    nodes: list[NodeExtensionInventory]
