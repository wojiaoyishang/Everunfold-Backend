"""Read-only, permission-filtered work queues; no synthetic dashboard counters."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings
from app.modules.auth.permissions import Actor
from app.modules.comments.schemas import WorkspaceItem, WorkspaceSummary
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.models import Comment, SiteSettings
from app.modules.settings.models import SettingsDeliveryEvent


async def workspace(session: AsyncSession, actor: Actor, config: Settings) -> WorkspaceSummary:
    result = WorkspaceSummary()
    if actor.has("comments:read"):
        result.pending_comments = int(
            await session.scalar(
                select(func.count()).select_from(Comment).where(Comment.status == "pending")
            )
            or 0
        )
    if actor.has("content:read"):
        conditions: list[ColumnElement[bool]] = [ContentDraft.deleted_at.is_(None)]
        if actor.permissions["content:read"] == "own":
            conditions.append(ContentDraft.created_by == actor.id)

        async def count(*filters: ColumnElement[bool]) -> int:
            return int(
                await session.scalar(
                    select(func.count()).select_from(ContentDraft).where(*conditions, *filters)
                )
                or 0
            )

        result.drafts = await count(ContentDraft.published_revision.is_(None))
        result.scheduled = await count(ContentDraft.scheduled_for.is_not(None))
        result.failed_schedules = await count(ContentDraft.schedule_error.is_not(None))
        original = aliased(ContentDraft)
        result.translations_to_review = int(
            await session.scalar(
                select(func.count())
                .select_from(ContentDraft)
                .join(TranslationGroup, TranslationGroup.id == ContentDraft.translation_group_id)
                .join(original, original.id == TranslationGroup.source_id)
                .where(
                    *conditions,
                    ContentDraft.id != original.id,
                    func.coalesce(ContentDraft.translated_from_revision, 0) < original.revision,
                )
            )
            or 0
        )

        def item(row: ContentDraft) -> WorkspaceItem:
            return WorkspaceItem(
                id=row.id,
                title=row.title,
                href=f"/admin/content/{row.type_id}/{row.id}/edit",
                updated_at=row.updated_at,
                scheduled_for=row.scheduled_for,
                error=row.schedule_error,
            )

        result.recent = [
            item(row)
            for row in await session.scalars(
                select(ContentDraft)
                .where(*conditions)
                .order_by(ContentDraft.updated_at.desc())
                .limit(6)
            )
        ]
        result.upcoming = [
            item(row)
            for row in await session.scalars(
                select(ContentDraft)
                .where(*conditions, ContentDraft.scheduled_for.is_not(None))
                .order_by(ContentDraft.scheduled_for)
                .limit(6)
            )
        ]
    if actor.has("settings:read"):
        site = await session.get(SiteSettings, 1)
        if site is None:
            return result
        nodes = [key for key, value in config.frontends.items() if value.enabled]
        current = [
            SettingsDeliveryEvent.source_epoch == site.source_epoch,
            SettingsDeliveryEvent.node_id.in_(nodes),
        ]
        latest = (
            select(
                SettingsDeliveryEvent.node_id,
                SettingsDeliveryEvent.resource,
                func.max(SettingsDeliveryEvent.revision).label("revision"),
            )
            .where(*current)
            .group_by(SettingsDeliveryEvent.node_id, SettingsDeliveryEvent.resource)
            .subquery()
        )
        result.failed_deliveries = int(
            await session.scalar(
                select(func.count())
                .select_from(SettingsDeliveryEvent)
                .join(
                    latest,
                    (latest.c.node_id == SettingsDeliveryEvent.node_id)
                    & (latest.c.resource == SettingsDeliveryEvent.resource)
                    & (latest.c.revision == SettingsDeliveryEvent.revision),
                )
                .where(
                    *current,
                    SettingsDeliveryEvent.applied_at.is_(None),
                    SettingsDeliveryEvent.error.is_not(None),
                )
            )
            or 0
        )
    return result
