"""Real PostgreSQL checks; opt-in and rollback-only in the dedicated test database."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ReaderPreviewConfig, load_settings
from app.core.database import Database
from app.entrypoints.api import create_app
from app.modules.content.models import (
    Comment,
    Content,
    ContentTaxonomy,
    SearchDocument,
    SiteSettings,
    Taxonomy,
)
from app.modules.content.repository import ContentRepository
from app.modules.content.router import get_session
from app.modules.content.service import PublicContentService

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("RUN_DATABASE_TESTS") != "1",
        reason="Set RUN_DATABASE_TESTS=1 with a migrated everunfold_test database",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def database_session() -> AsyncIterator[AsyncSession]:
    settings = load_settings(os.environ.get("TEST_CONFIG_FILE", "config.test.local.toml"))
    url = make_url(settings.database.url.get_secret_value())
    if url.database != "everunfold_test" or not settings.redis.namespace_scope.startswith("test"):
        pytest.fail("Integration tests refuse non-test database or namespace configuration")
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await database.close()


async def seed_public_case(session: AsyncSession) -> dict[str, Any]:
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    items = []
    for index, (kind, status, visibility) in enumerate(
        [
            ("article", "published", "public"),
            ("article", "published", "public"),
            ("page", "published", "public"),
            ("article", "published", "authenticated"),
            ("article", "draft", "public"),
            ("article", "withdrawn", "public"),
            ("article", "published", "unlisted"),
        ]
    ):
        item = Content(
            id=uuid.uuid4(),
            source="test",
            kind=kind,
            title=f"中文检索 {index}" if index == 0 else f"Item {index}",
            path=f"/integration-{uuid.uuid4()}.html",
            excerpt="公开摘要",
            published_at=now + timedelta(minutes=index),
            updated_at=now,
            author="Public author",
            publication_status=status,
            visibility=visibility,
            content=[{"id": "block-1", "type": "html", "content": "<p>你好世界</p>"}],
            media_urls={"m_demo": {"original": "/media/demo.webp"}},
        )
        session.add(item)
        items.append(item)
    await session.flush()
    for index, item in enumerate(items):
        session.add(
            SearchDocument(
                content_id=item.id,
                title=item.title,
                body="中文内容 标记100%" if index == 0 else "other body",
            )
        )
    public_category = Taxonomy(id=uuid.uuid4(), kind="category", name="公开分类", slug="public")
    private_category = Taxonomy(id=uuid.uuid4(), kind="category", name="私有分类", slug="private")
    session.add_all([public_category, private_category])
    await session.flush()
    session.add_all(
        [
            ContentTaxonomy(content_id=items[0].id, taxonomy_id=public_category.id),
            ContentTaxonomy(content_id=items[3].id, taxonomy_id=public_category.id),
            ContentTaxonomy(content_id=items[3].id, taxonomy_id=private_category.id),
        ]
    )
    parent = Comment(
        id=uuid.uuid4(), content_id=items[0].id, author="甲", content="公开留言", created_at=now
    )
    hidden = Comment(
        id=uuid.uuid4(),
        content_id=items[0].id,
        author="不可见用户",
        content="不可见留言",
        created_at=now,
        status="hidden",
    )
    session.add_all([parent, hidden])
    await session.flush()
    session.add_all(
        [
            Comment(
                id=uuid.uuid4(),
                content_id=items[0].id,
                parent_id=parent.id,
                author="乙",
                content="回复甲",
                created_at=now + timedelta(seconds=1),
            ),
            Comment(
                id=uuid.uuid4(),
                content_id=items[0].id,
                parent_id=hidden.id,
                author="丙",
                content="不泄露隐藏父评论",
                created_at=now + timedelta(seconds=2),
            ),
            Comment(
                id=uuid.uuid4(),
                content_id=items[0].id,
                author="待审",
                content="待审不可见",
                created_at=now,
                status="pending",
            ),
            Comment(
                id=uuid.uuid4(),
                content_id=items[3].id,
                author="私有",
                content="私有不可见",
                created_at=now,
            ),
        ]
    )
    session.add(
        SiteSettings(
            id=1,
            data={
                "name": "测试站点",
                "description": "仅测试",
                "author": "Public author",
                "avatar_url": None,
                "hero_url": None,
                "hero_title": "Hello",
                "hero_subtitle": "World",
                "notice": "",
                "navigation": [{"label": "首页", "href": "/"}],
            },
        )
    )
    await session.flush()
    return {"contents": items}


async def test_public_api_filters_pagination_search_and_comment_privacy(
    database_session: AsyncSession,
) -> None:
    seeded = await seed_public_case(database_session)
    items = seeded["contents"]
    settings = load_settings("config.test.local.toml")
    application = create_app(settings)

    async def shared_session() -> AsyncIterator[AsyncSession]:
        yield database_session

    application.dependency_overrides[get_session] = shared_session
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client,
    ):
        response = await client.get("/v1/contents", params={"kind": "article", "page_size": 1})
        assert response.status_code == 200
        assert response.json()["data"]["total"] == 2
        assert len(response.json()["data"]["items"]) == 1
        assert response.json()["data"]["items"][0]["id"] == str(items[1].id)
        assert "source_id" not in response.text
        search = await client.get("/v1/contents", params={"q": "中文"})
        assert search.json()["data"]["total"] == 1
        wildcard = await client.get("/v1/contents", params={"q": "%"})
        assert wildcard.json()["data"]["total"] == 1
        category = await client.get("/v1/contents", params={"category": "public"})
        assert category.json()["data"]["total"] == 1
        taxonomies = (await client.get("/v1/taxonomies")).json()["data"]["items"]
        assert [item["name"] for item in taxonomies] == ["公开分类"]
        assert taxonomies[0]["count"] == 1
        for item in items[3:]:
            hidden = await client.get("/v1/contents/resolve", params={"path": item.path})
            assert hidden.status_code == 404
            hidden_comments = await client.get(f"/v1/contents/{item.id}/comments")
            assert hidden_comments.status_code == 404
        detail = await client.get("/v1/contents/resolve", params={"path": items[0].path})
        assert detail.json()["data"]["content"][0]["id"] == "block-1"
        comments = await client.get(
            f"/v1/contents/{items[0].id}/comments", params={"page": 2, "page_size": 1}
        )
        assert comments.json()["data"]["total"] == 3
        assert comments.json()["data"]["items"][0]["reply_to"] == "甲"
        last = await client.get(
            f"/v1/contents/{items[0].id}/comments", params={"page": 3, "page_size": 1}
        )
        assert last.json()["data"]["items"][0]["parent_id"] is None
        assert last.json()["data"]["items"][0]["reply_to"] is None
        beyond = await client.get("/v1/contents", params={"page": 1000})
        assert beyond.json()["data"]["items"] == []
        invalid = await client.get("/v1/contents", params={"page_size": 101})
        assert invalid.status_code == 422
        assert invalid.json()["success"] is False
        write = await client.post("/v1/contents")
        assert write.status_code == 405


async def test_snapshot_contains_only_public_projection_and_consistent_stats(
    database_session: AsyncSession,
) -> None:
    seeded = await seed_public_case(database_session)
    service = PublicContentService(ContentRepository(database_session))
    snapshot = await service.snapshot()
    assert len(snapshot.contents) == 3
    assert snapshot.site.stats.articles == 2
    assert snapshot.site.stats.pages == 1
    assert snapshot.site.stats.comments == 3
    assert snapshot.site.stats.categories == 1
    assert snapshot.site.stats.tags == 0
    assert len(snapshot.comments[str(seeded["contents"][0].id)]) == 3
    assert "不可见用户" not in snapshot.model_dump_json()
    assert "source_id" not in snapshot.model_dump_json()
    assert snapshot.generation == 1
    assert snapshot.source_epoch
    # This read transaction must not persist fixtures, create schemas, or write Redis state.
    assert await database_session.scalar(text("SELECT current_database()")) == "everunfold_test"


async def test_reader_preview_filters_visibility_without_writing(
    database_session: AsyncSession,
) -> None:
    seeded = await seed_public_case(database_session)
    items = seeded["contents"]
    settings = load_settings("config.test.local.toml").model_copy(
        update={"reader_preview": ReaderPreviewConfig(enabled=True)}
    )
    application = create_app(settings)

    async def shared_session() -> AsyncIterator[AsyncSession]:
        yield database_session

    application.dependency_overrides[get_session] = shared_session
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client,
    ):
        ids = [item.id for item in items] + [items[0].id, uuid.uuid4()]
        before = (
            await PublicContentService(ContentRepository(database_session)).snapshot()
        ).contents
        first = await client.get(
            "/v1/reader-preview/metrics", params=[("ids", str(i)) for i in ids]
        )
        second = await client.get(
            "/v1/reader-preview/metrics", params=[("ids", str(i)) for i in ids]
        )
        assert first.status_code == 200
        data = first.json()["data"]
        assert data["mode"] == "demo"
        assert {item["content_id"] for item in data["items"]} == {str(i.id) for i in items[:3]}
        assert len(data["items"]) == 3
        assert data["items"] == second.json()["data"]["items"]
        assert "no-store" in first.headers["cache-control"]
        after = (
            await PublicContentService(ContentRepository(database_session)).snapshot()
        ).contents
        assert [item.model_dump() for item in before] == [item.model_dump() for item in after]
