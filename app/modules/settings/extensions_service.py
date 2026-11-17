"""Extension activation is data; executable packages remain deployment-owned."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FrontendConfig, Settings
from app.core.plugins.loader import plugin_catalog
from app.core.plugins.state import CATALOG_KEY, installed_info
from app.modules.auth.models import AuthAuditEvent
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.media.locking import lock_references
from app.modules.pages.registration import install_manifest
from app.modules.settings.extensions_schemas import (
    ExtensionCatalog,
    ExtensionInventory,
    ExtensionPreferences,
    NodeExtensionInventory,
)
from app.modules.settings.models import FrontendExtensionInventory
from app.modules.settings.node_registry import read_targets, registry_record
from app.modules.settings.service import SettingsService


async def catalog(session: AsyncSession, config: Settings) -> ExtensionCatalog:
    records = {
        row.node_id: row for row in await session.scalars(select(FrontendExtensionInventory))
    }
    site = await session.get(SiteSettings, 1)
    data = site.data if site else {}
    plugins = plugin_catalog()
    infos = {item.id: item for item in installed_info(data)}
    infos.update({item.info.id: item.info for item in plugins.plugins})
    return ExtensionCatalog(
        backend_plugins=list(infos.values()),
        backend_in_sync=data.get(CATALOG_KEY) == plugins.snapshot(),
        nodes=[
            NodeExtensionInventory(
                node_id=node,
                enabled=target.enabled,
                reported_at=records[node].reported_at if node in records else None,
                inventory=ExtensionInventory.model_validate(records[node].data)
                if node in records
                else None,
            )
            for node, target in (await read_targets(session, config)).items()
        ],
    )


async def install_inventory(
    session: AsyncSession,
    config: Settings,
    node: str,
    value: ExtensionInventory,
    *,
    authorized_target: FrontendConfig | None = None,
    public_url: str | None = None,
) -> None:
    await lock_references(session)
    await SettingsService(session, config).site(locked=True)
    target = (await read_targets(session, config)).get(node)
    if authorized_target is not None and (target != authorized_target or not target.enabled):
        raise AuthFailure(403, 40320, "内部请求未通过验证")
    if target is None:
        raise AuthFailure(404, 40420, "节点不存在")
    from app.modules.settings.backend_plugins import validate_names

    validate_names(plugin_catalog(), value)
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
    if public_url is not None and authorized_target is not None:
        registry = await registry_record(session, lock=True)
        if registry is not None and target.base_url != public_url:
            registry.nodes = registry.nodes | {
                node: {**registry.nodes[node], "base_url": public_url}
            }
            registry.revision += 1
            session.add(
                AuthAuditEvent(
                    action="frontend_node_public_url",
                    created_at=datetime.now(UTC),
                    details={
                        "node_id": node,
                        "before": target.base_url,
                        "after": public_url,
                        "revision": registry.revision,
                        "actor": "authenticated_frontend",
                    },
                )
            )
    await session.commit()


async def validate_activation(
    session: AsyncSession,
    config: Settings,
    value: ExtensionPreferences,
    before: ExtensionPreferences,
) -> None:
    if (value.enabled_plugins is None) != (before.enabled_plugins is None):
        raise AuthFailure(422, 42298, "请刷新设置后再修改插件")
    inventories = await catalog(session, config)
    enabled = [node for node in inventories.nodes if node.enabled]
    backend_ids = {item.id for item in inventories.backend_plugins}
    for plugin in inventories.backend_plugins:
        if value.enables(plugin.id) and any(
            not value.enables(dependency) for dependency in plugin.requires
        ):
            raise AuthFailure(422, 42298, "请同时启用依赖插件，或先停用依赖它的插件")
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
    for plugin_id in sorted(value.changed_plugins(before)):
        if not value.enables(plugin_id):
            continue
        if plugin_id in backend_ids:
            if not inventories.backend_in_sync:
                raise AuthFailure(422, 42298, "后端插件尚未完成部署登记，请先同步插件目录")
            if not any(
                node.inventory and any(p.id == plugin_id for p in node.inventory.plugins)
                for node in enabled
            ):
                continue
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
