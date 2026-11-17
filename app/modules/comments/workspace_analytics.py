"""Bounded, permission-filtered reports. Never return audit payloads or visitor identities."""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.permissions import Actor
from app.modules.comments.schemas import (
    WorkspaceAnalytics,
    WorkspaceAudit,
    WorkspaceAuditItem,
    WorkspacePopularArticle,
    WorkspaceViewPoint,
)
from app.modules.comments.workspace_models import ArticleViewDay, WorkspaceAnalyticsEpoch
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content
from app.modules.settings.models import SettingsAudit


async def analytics(session: AsyncSession, actor: Actor, days: int) -> WorkspaceAnalytics:
    now = datetime.now(UTC)
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, UTC)
    epoch = await session.get(WorkspaceAnalyticsEpoch, 1)
    result = WorkspaceAnalytics(
        days=days,
        started_at=epoch.started_at if epoch else None,
        period_start=start,
        period_end=now,
    )
    if actor.has("content:read"):
        conditions = [Content.kind == "article", Content.publication_status == "published"]
        if actor.permissions["content:read"] == "own":
            conditions.append(
                Content.id.in_(
                    select(ContentDraft.id).where(
                        ContentDraft.created_by == actor.id, ContentDraft.deleted_at.is_(None)
                    )
                )
            )
        result.total_views = int(
            await session.scalar(select(func.sum(Content.view_count)).where(*conditions)) or 0
        )
        all_time = await session.execute(
            select(Content.id, Content.title, Content.view_count)
            .where(*conditions, Content.view_count > 0)
            .order_by(Content.view_count.desc(), Content.id)
            .limit(8)
        )
        result.all_time = [
            WorkspacePopularArticle(
                id=id_, title=title, href=f"/admin/content/article/{id_}/edit", views=int(views)
            )
            for id_, title, views in all_time
        ]
        period = [ArticleViewDay.day >= start.date(), ArticleViewDay.day <= now.date()]
        series = dict(
            (
                await session.execute(
                    select(ArticleViewDay.day, func.sum(ArticleViewDay.views))
                    .join(Content, Content.id == ArticleViewDay.content_id)
                    .where(*conditions, *period)
                    .group_by(ArticleViewDay.day)
                )
            ).all()
        )
        result.daily = [
            WorkspaceViewPoint(
                day=(start.date() + timedelta(days=i)).isoformat(),
                views=int(series.get(start.date() + timedelta(days=i), 0)),
            )
            for i in range(days)
        ]
        result.period_views = sum(point.views for point in result.daily)
        total = func.sum(ArticleViewDay.views)
        ranked = await session.execute(
            select(Content.id, Content.title, total)
            .join(ArticleViewDay, ArticleViewDay.content_id == Content.id)
            .where(*conditions, *period)
            .group_by(Content.id, Content.title)
            .order_by(total.desc(), Content.id)
            .limit(8)
        )
        result.popular = [
            WorkspacePopularArticle(
                id=id_, title=title, href=f"/admin/content/article/{id_}/edit", views=int(views)
            )
            for id_, title, views in ranked
        ]
    if actor.has("users:manage"):
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(AuthAuditEvent)
                .where(AuthAuditEvent.created_at >= start, AuthAuditEvent.created_at <= now)
            )
            or 0
        )
        rows = await session.execute(
            select(AuthAuditEvent, User.display_name)
            .outerjoin(User, User.id == AuthAuditEvent.user_id)
            .where(AuthAuditEvent.created_at >= start, AuthAuditEvent.created_at <= now)
            .order_by(AuthAuditEvent.created_at.desc(), AuthAuditEvent.id)
            .limit(8)
        )
        result.security = WorkspaceAudit(
            count=count,
            items=[
                WorkspaceAuditItem(
                    id=row.id, action=row.action, actor=name or "—", created_at=row.created_at
                )
                for row, name in rows
            ],
        )
    if actor.has("settings:read"):
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(SettingsAudit)
                .where(SettingsAudit.created_at >= start, SettingsAudit.created_at <= now)
            )
            or 0
        )
        setting_rows = await session.execute(
            select(SettingsAudit, User.display_name)
            .outerjoin(User, User.id == SettingsAudit.user_id)
            .where(SettingsAudit.created_at >= start, SettingsAudit.created_at <= now)
            .order_by(SettingsAudit.created_at.desc(), SettingsAudit.id)
            .limit(8)
        )
        result.settings = WorkspaceAudit(
            count=count,
            items=[
                WorkspaceAuditItem(
                    id=row.id, action=row.resource, actor=name or "—", created_at=row.created_at
                )
                for row, name in setting_rows
            ],
        )
    return result
