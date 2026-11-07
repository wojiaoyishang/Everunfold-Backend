"""Extension activation is data; executable packages remain deployment-owned."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.pages.registration import install_manifest
from app.modules.settings.extensions_schemas import (
    ExtensionCatalog,
    ExtensionInventory,
    ExtensionPreferences,
    NodeExtensionInventory,
)
from app.modules.settings.models import FrontendExtensionInventory


async def catalog(session: AsyncSession, config: Settings) -> ExtensionCatalog:
    records = {
        row.node_id: row for row in await session.scalars(select(FrontendExtensionInventory))
    }
    return ExtensionCatalog(
        nodes=[
            NodeExtensionInventory(
                node_id=node,
                enabled=target.enabled,
                reported_at=records[node].reported_at if node in records else None,
                inventory=ExtensionInventory.model_validate(records[node].data)
                if node in records
                else None,
            )
            for node, target in config.frontends.items()
        ]
    )


async def install_inventory(
    session: AsyncSession, config: Settings, node: str, value: ExtensionInventory
) -> None:
    if node not in config.frontends:
        raise AuthFailure(404, 40420, "节点不存在")
    # The page installer takes the same site lock as activation and preserves existing data.
    await install_manifest(session, config, node, value.pages, commit=False)
    record = await session.get(FrontendExtensionInventory, node)
    encoded = value.model_dump(mode="json")
    if record is None:
        session.add(
            FrontendExtensionInventory(node_id=node, data=encoded, reported_at=datetime.now(UTC))
        )
    else:
        record.data, record.reported_at = encoded, datetime.now(UTC)
    await session.commit()


async def validate_activation(
    session: AsyncSession,
    config: Settings,
    value: ExtensionPreferences,
    before: ExtensionPreferences,
) -> None:
    inventories = await catalog(session, config)
    enabled = [node for node in inventories.nodes if node.enabled]
    if value.theme and value.theme != before.theme:
        definitions = [
            next((theme for theme in node.inventory.themes if theme.id == value.theme), None)
            if node.inventory
            else None
            for node in enabled
        ]
        if (
            not definitions
            or any(theme is None for theme in definitions)
            or len({theme.model_dump_json() for theme in definitions if theme is not None}) != 1
        ):
            raise AuthFailure(422, 42298, "主题尚未在所有启用节点部署，请先完成部署登记")
    for plugin_id in set(before.disabled_plugins) - set(value.disabled_plugins):
        plugin_definitions = [
            next((plugin for plugin in node.inventory.plugins if plugin.id == plugin_id), None)
            if node.inventory
            else None
            for node in enabled
        ]
        if (
            not plugin_definitions
            or any(plugin is None for plugin in plugin_definitions)
            or len({plugin.build for plugin in plugin_definitions if plugin is not None}) != 1
        ):
            raise AuthFailure(422, 42298, "插件尚未在所有启用节点部署，请先完成部署登记")
