"""Applied activation determines whether registered page capabilities are reachable."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.models import SiteSettings
from app.modules.settings.defaults import public_settings
from app.modules.settings.extensions_schemas import ExtensionInventory
from app.modules.settings.models import FrontendExtensionInventory


async def disabled_page_ids(session: AsyncSession) -> set[str]:
    site = await session.get(SiteSettings, 1)
    preferences = public_settings(site.data if site else {}).extensions
    if preferences.enabled_plugins is None and not preferences.disabled_plugins:
        return set()
    return {
        page_id
        for row in await session.scalars(select(FrontendExtensionInventory))
        for plugin in ExtensionInventory.model_validate(row.data).plugins
        if not preferences.enables(plugin.id)
        for page_id in plugin.page_ids
    }
