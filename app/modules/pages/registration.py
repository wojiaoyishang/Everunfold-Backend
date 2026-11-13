"""Explicit deployment registration; never accepts browser-controlled manifests."""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.media.locking import lock_references
from app.modules.pages.models import FrontendPageRegistration
from app.modules.pages.schemas import PageManifest
from app.modules.settings.node_registry import read_targets
from app.modules.settings.service import SettingsService


async def install_manifest(
    session: AsyncSession,
    config: Settings,
    node: str,
    manifest: PageManifest,
    *,
    commit: bool = True,
) -> None:
    await lock_references(session)
    await SettingsService(session, config).site(locked=True)
    if node not in await read_targets(session, config):
        raise ValueError("unknown frontend node")
    await session.execute(
        delete(FrontendPageRegistration).where(FrontendPageRegistration.node_id == node)
    )
    session.add_all(
        FrontendPageRegistration(
            node_id=node, page_id=definition.id, definition=definition.model_dump(mode="json")
        )
        for definition in manifest.pages
    )
    if commit:
        await session.commit()
