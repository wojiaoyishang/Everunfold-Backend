"""Page types are deployment capabilities shared by any number of authored pages."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.pages.availability import disabled_page_ids
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.schemas import (
    CUSTOM_PAGE_TYPE,
    PageRegistration,
    PageTypeCatalog,
    PageTypeItem,
)
from app.modules.settings.node_registry import read_targets

CUSTOM_PAGE_TITLE = "自定义代码"


@dataclass(frozen=True)
class RegisteredPageType:
    definition: PageRegistration
    available: bool


async def registered_types(
    session: AsyncSession, config: Settings, identity: str | None = None
) -> dict[str, RegisteredPageType]:
    statement = select(FrontendPageRegistration).order_by(
        FrontendPageRegistration.page_id, FrontendPageRegistration.node_id
    )
    if identity is not None:
        statement = statement.where(FrontendPageRegistration.page_id == identity)
    groups: dict[str, list[FrontendPageRegistration]] = {}
    for row in await session.scalars(statement):
        groups.setdefault(row.page_id, []).append(row)
    if not groups:
        return {}
    targets = await read_targets(session, config)
    enabled = {node for node, target in targets.items() if target.enabled}
    disabled = await disabled_page_ids(session)
    result = {}
    for type_id, rows in groups.items():
        active = [row for row in rows if row.node_id in enabled]
        definitions = [PageRegistration.model_validate(row.definition) for row in active or rows]
        first = definitions[0]
        available = (
            bool(enabled)
            and type_id not in disabled
            and len(active) == len(enabled)
            and all(definition == first for definition in definitions)
        )
        result[type_id] = RegisteredPageType(first, available)
    return result


async def page_types(session: AsyncSession, config: Settings) -> PageTypeCatalog:
    counts = dict(
        (
            await session.execute(
                select(ManagedPage.type_id, func.count())
                .where(ManagedPage.deleted_at.is_(None))
                .group_by(ManagedPage.type_id)
            )
        ).all()
    )
    types = await registered_types(session, config)
    items = [
        PageTypeItem(
            id=CUSTOM_PAGE_TYPE,
            title=CUSTOM_PAGE_TITLE,
            description="使用 HTML、CSS 和 JavaScript 编写页面。",
            source="custom",
            available=True,
            page_count=counts.get(CUSTOM_PAGE_TYPE, 0),
        )
    ]
    for identity in sorted((counts.keys() | types.keys()) - {CUSTOM_PAGE_TYPE}):
        registered = types.get(identity)
        definition = registered.definition if registered else None
        items.append(
            PageTypeItem(
                id=identity,
                title=definition.title if definition else identity,
                description=definition.description if definition else "",
                source="registered",
                registration=definition,
                available=registered.available if registered else False,
                page_count=counts.get(identity, 0),
            )
        )
    return PageTypeCatalog(items=items)
