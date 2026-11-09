"""Explicit deployment registration; never accepts browser-controlled manifests."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.media.locking import lock_references
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.paths import content_path_taken, page_path_taken
from app.modules.pages.schemas import PageManifest, PageValue
from app.modules.settings.languages import language_catalog, reserved_language_path
from app.modules.settings.service import SettingsService


async def install_manifest(
    session: AsyncSession,
    config: Settings,
    node: str,
    manifest: PageManifest,
    *,
    commit: bool = True,
) -> None:
    if node not in config.frontends:
        raise ValueError("unknown frontend node")
    await lock_references(session)
    await SettingsService(session, config).site(locked=True)
    await session.execute(
        delete(FrontendPageRegistration).where(FrontendPageRegistration.node_id == node)
    )
    languages = await language_catalog(session)
    for page in manifest.pages:
        defaults = {field.key: field.default for field in page.fields} | page.defaults
        session.add(
            FrontendPageRegistration(
                node_id=node, page_id=page.id, definition=page.model_dump(mode="json")
            )
        )
        identity = uuid5(NAMESPACE_URL, "everunfold:pages:" + page.id)
        row = await session.get(ManagedPage, identity)
        if row is None:
            path, suffix = page.path, 1
            while (
                reserved_language_path(path, languages)
                or await page_path_taken(session, path)
                or await content_path_taken(session, path)
            ):
                path = f"/page-{identity.hex[:12]}-{suffix}"
                suffix += 1
            row = ManagedPage(
                id=identity,
                source="registered",
                implementation_id=page.id,
                revision=1,
                data=PageValue(
                    title=page.title,
                    path=path,
                    description=page.description,
                    comments_open=page.comments_open,
                    options=defaults,
                ).model_dump(mode="json"),
            )
            session.add(row)
    if commit:
        await session.commit()
