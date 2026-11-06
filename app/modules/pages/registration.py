"""Explicit deployment registration; never accepts browser-controlled manifests."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.media.locking import lock_references
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.options import validate_options
from app.modules.pages.schemas import PageManifest, PageValue
from app.modules.settings.service import SettingsService


async def install_manifest(
    session: AsyncSession, config: Settings, node: str, manifest: PageManifest
) -> None:
    if node not in config.frontends:
        raise ValueError("unknown frontend node")
    await lock_references(session)
    await SettingsService(session, config).site(locked=True)
    await session.execute(
        delete(FrontendPageRegistration).where(FrontendPageRegistration.node_id == node)
    )
    for page in manifest.pages:
        defaults = validate_options(page.fields, {})
        session.add(
            FrontendPageRegistration(
                node_id=node, page_id=page.id, definition=page.model_dump(mode="json")
            )
        )
        identity = uuid5(NAMESPACE_URL, "everunfold:pages:" + page.id)
        row = await session.get(ManagedPage, identity)
        if row is None:
            row = ManagedPage(
                id=identity,
                source="registered",
                implementation_id=page.id,
                revision=1,
                data=PageValue(
                    title=page.title, path=page.path, description=page.description, options=defaults
                ).model_dump(mode="json"),
            )
            session.add(row)
    await session.commit()
