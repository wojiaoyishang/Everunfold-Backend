"""Explicit, idempotent deployment of backend contributions into the reliable settings stream."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins.loader import PluginCatalog, plugin_catalog
from app.core.plugins.state import CATALOG_KEY, installed_info
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.cache_events import stage_cache_events
from app.modules.settings.defaults import public_settings
from app.modules.settings.extensions_schemas import ExtensionInventory
from app.modules.settings.models import FrontendExtensionInventory, SettingsAudit
from app.modules.settings.node_registry import read_targets
from app.modules.settings.service import SettingsService


def validate_names(catalog: PluginCatalog, inventory: ExtensionInventory) -> None:
    reserved = {name for plugin in catalog.plugins for name in plugin.info.shortcodes}
    names: set[str] = set()
    for plugin in inventory.plugins:
        for name in plugin.shortcodes:
            if name in reserved or name in names or name.startswith("custom:"):
                raise AuthFailure(422, 42298, "插件短代码名称与已登记的定义冲突")
            names.add(name)


async def install_backend_plugins(session: AsyncSession, config: Settings) -> bool:
    catalog = plugin_catalog()
    await lock_references(session)
    service = SettingsService(session, config)
    site = await service.site(locked=True)
    snapshot = catalog.snapshot()
    if site.data.get(CATALOG_KEY) == snapshot:
        return False
    for row in await session.scalars(select(FrontendExtensionInventory)):
        validate_names(catalog, ExtensionInventory.model_validate(row.data))
    disabled = set(site.data.get("extensions", {}).get("disabled_plugins", []))
    for plugin in catalog.plugins:
        if plugin.info.id not in disabled and set(plugin.info.requires) & disabled:
            raise AuthFailure(422, 42298, "请同时启用依赖插件，或先停用依赖它的插件")
    before = {item.id: item.build for item in installed_info(site.data)}
    data = {**site.data, CATALOG_KEY: snapshot}
    value = await service.resolve(public_settings(data))
    site.data = data
    site.generation += 1
    await stage_cache_events(
        session, site, "public-settings", site.generation, (CacheChange("settings_changed"),)
    )
    session.add(
        SettingsAudit(
            user_id=UUID(int=0),
            resource="backend-plugins",
            revision=site.generation,
            changes={
                "before": before,
                "after": {item.info.id: item.info.build for item in catalog.plugins},
            },
        )
    )
    for node, target in (await read_targets(session, config)).items():
        if target.enabled and target.settings_receiver_url:
            await service.enqueue(
                node, site.source_epoch, "public-settings", site.generation, value
            )
    await session.commit()
    return True
