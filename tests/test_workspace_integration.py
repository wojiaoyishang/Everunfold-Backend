"""Read reports from PostgreSQL while preserving ownership and audit capabilities."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import Actor
from app.modules.comments.workspace_analytics import analytics
from app.modules.comments.workspace_models import ArticleViewDay
from app.modules.content.models import Content
from tests.test_public_content_integration import database_session as database_session
from tests.test_public_content_integration import isolated_settings as isolated_settings
from tests.test_public_content_integration import pytestmark as pytestmark


async def test_workspace_own_scope_and_no_audit_payloads(database_session: AsyncSession) -> None:
    id_ = uuid4()
    now = datetime.now(UTC)
    database_session.add(
        Content(
            id=id_,
            source="test",
            kind="article",
            title="Foreign private title",
            path="/test-workspace-" + str(id_),
            published_at=now,
            updated_at=now,
            author="Fixture",
            view_count=40,
        )
    )
    await database_session.flush()
    database_session.add(ArticleViewDay(content_id=id_, day=now.date(), views=4))
    await database_session.flush()
    actor = Actor(uuid4(), "test", "Test", "user", {"content:read": "own"})
    report = await analytics(database_session, actor, 7)
    assert report.total_views == 0 and report.period_views == 0
    assert report.all_time == [] and report.popular == []
    assert report.security is None and report.settings is None
    assert "Foreign private title" not in report.model_dump_json()
    viewer = Actor(uuid4(), "test", "Test", "user", {"content:read": "all"})
    allowed = await analytics(database_session, viewer, 7)
    assert any(row.id == id_ and row.views == 4 for row in allowed.popular)
    assert allowed.security is None and allowed.settings is None
