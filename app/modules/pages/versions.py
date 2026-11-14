"""Private immutable source snapshots for managed pages and reusable templates."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import AuthSubject
from app.modules.auth.service import AuthFailure
from app.modules.pages.groups import source_value
from app.modules.pages.models import (
    ManagedPage,
    ManagedPageRevision,
    PageTemplate,
    PageTemplateRevision,
)
from app.modules.pages.schemas import (
    PageValue,
    PageVersion,
    PageVersionCatalog,
    RevisionAction,
    TitledPageCode,
)

VERSION_BATCH = 20
VersionRow = ManagedPageRevision | PageTemplateRevision


def record_version(
    session: AsyncSession,
    owner: ManagedPage | PageTemplate,
    value: PageValue | TitledPageCode,
    actor: AuthSubject,
    action: RevisionAction,
    *,
    from_revision: int | None = None,
) -> None:
    model = ManagedPageRevision if isinstance(owner, ManagedPage) else PageTemplateRevision
    session.add(
        model(
            owner=owner,
            revision=owner.revision,
            value=value.model_dump(mode="json"),
            actor=actor.display_name or actor.username,
            action=action,
            created_at=datetime.now(UTC),
            from_revision=from_revision,
        )
    )


def record_page_version(
    session: AsyncSession,
    page: ManagedPage,
    actor: AuthSubject,
    action: RevisionAction,
    *,
    from_revision: int | None = None,
) -> None:
    record_version(
        session, page, source_value(page.data), actor, action, from_revision=from_revision
    )


async def version_catalog(
    session: AsyncSession, owner: ManagedPage | PageTemplate, before: int | None
) -> PageVersionCatalog:
    model: type[ManagedPageRevision] | type[PageTemplateRevision]
    if isinstance(owner, ManagedPage):
        model = ManagedPageRevision
        title = owner.data["title"]
        archived = owner.deleted_at is not None
    else:
        model = PageTemplateRevision
        title = owner.draft_value["title"] if owner.draft_value is not None else owner.title
        archived = False
    # Select metadata only. Source may be large and is fetched for the chosen versions.
    statement = select(
        model.revision, model.actor, model.created_at, model.action, model.from_revision
    ).where(model.owner_id == owner.id, model.revision <= owner.revision)
    if before is not None:
        statement = statement.where(model.revision < before)
    rows = (
        (await session.execute(statement.order_by(model.revision.desc()).limit(VERSION_BATCH + 1)))
        .mappings()
        .all()
    )
    items = [PageVersion.model_validate(row) for row in rows[:VERSION_BATCH]]
    return PageVersionCatalog(
        title=title,
        revision=owner.revision,
        applied_revision=owner.applied_revision,
        archived=archived,
        items=items,
        next_before=items[-1].revision if len(rows) > VERSION_BATCH else None,
    )


async def get_version(
    session: AsyncSession, kind: Literal["page", "template"], identity: UUID, revision: int
) -> VersionRow:
    key = {"owner_id": identity, "revision": revision}
    row = (
        await session.get(ManagedPageRevision, key)
        if kind == "page"
        else await session.get(PageTemplateRevision, key)
    )
    if row is None:
        raise AuthFailure(404, 40498, "此版本不存在")
    return row


def version_metadata(row: VersionRow) -> PageVersion:
    return PageVersion.model_validate(row, from_attributes=True)
