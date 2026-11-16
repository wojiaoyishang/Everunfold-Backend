"""Real PostgreSQL checks; opt-in and rollback-only in the dedicated test database."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CORSConfig, ReaderPreviewConfig, Settings, load_settings
from app.core.database import Database
from app.entrypoints.api import create_app
from app.modules.auth.models import AuthRateLimit
from app.modules.auth.security import hash_password
from app.modules.content.comment_likes import CommentLikeService
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
def isolated_settings() -> Settings:
    settings = load_settings(os.environ.get("TEST_CONFIG_FILE", "config.test.local.toml"))
    url = make_url(settings.database.url.get_secret_value())
    if url.database != "everunfold_test" or not settings.redis.namespace_scope.startswith("test"):
        pytest.fail("Integration tests refuse non-test database or namespace configuration")
    return settings


@pytest.fixture
async def database_session(isolated_settings: Settings) -> AsyncIterator[AsyncSession]:
    database = Database(isolated_settings.database.url.get_secret_value())
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
    # Initialization now creates a singleton; isolate this fixture inside its rollback.
    await session.execute(delete(SiteSettings))
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
                "navigation": [{"label": "首页", "labels": {"en": "Home"}, "href": "/"}],
                "extensions": {"theme": "argon"},
                "theme_options": {
                    "argon": {
                        "navigation": [
                            {
                                "id": "theme-home",
                                "label": "首页",
                                "labels": {"en": "Theme home"},
                                "href": "/",
                            }
                        ]
                    }
                },
            },
        )
    )
    await session.flush()
    return {"contents": items}


async def test_comment_likes_are_confirmed_idempotent_and_private(
    database_session: AsyncSession,
) -> None:
    seeded = await seed_public_case(database_session)
    comments = list(
        await database_session.scalars(
            select(Comment).where(Comment.content_id == seeded["contents"][0].id)
        )
    )
    visible = next(item for item in comments if item.status == "approved")
    hidden = next(item for item in comments if item.status == "hidden")
    service = CommentLikeService(database_session)
    visitor, other = uuid.uuid4(), uuid.uuid4()
    assert (await service.read([visible.id], visitor))[0].count == 0
    assert await service.read([hidden.id, uuid.uuid4()], visitor) == []
    first = await service.set(visible.id, visitor, True)
    assert first.liked and first.count == 1
    assert (await service.set(visible.id, visitor, True)).count == 1
    other_state = (await service.read([visible.id], other))[0]
    assert not other_state.liked and other_state.count == 1
    assert (await service.set(visible.id, other, True)).count == 2
    final = await service.set(visible.id, visitor, False)
    assert not final.liked and final.count == 1
    assert (await service.set(visible.id, visitor, False)).count == 1
    # Filtering is rechecked on every read; a stale snapshot cannot grant access.
    visible.status = "hidden"
    await database_session.flush()
    assert await service.read([visible.id], other) == []


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
        site = await client.get("/v1/site")
        assert site.status_code == 200
        assert site.json()["data"]["navigation"][0]["labels"] == {"en": "Theme home"}
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
    assert snapshot.site.navigation[0].labels == {"en": "Theme home"}
    assert len(snapshot.comments[str(seeded["contents"][0].id)]) == 3
    assert "不可见用户" not in snapshot.model_dump_json()
    assert "source_id" not in snapshot.model_dump_json()
    assert snapshot.generation == 1
    assert snapshot.source_epoch
    # This read transaction must not persist fixtures, create schemas, or write Redis state.
    assert await database_session.scalar(text("SELECT current_database()")) == "everunfold_test"


@pytest.fixture
async def locked_article(
    isolated_settings: Settings,
) -> AsyncIterator[tuple[Content, list[Taxonomy], str, str]]:
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex
    password = "Local article password"
    article = Content(
        id=uuid.uuid4(),
        source="test",
        kind="article",
        title="Visible title",
        path=f"/protected-{suffix}.html",
        excerpt="private excerpt",
        published_at=now,
        updated_at=now,
        author="Public author",
        publication_status="published",
        visibility="password",
        access_password_hash=hash_password(password),
        cover_url="/media/cover.webp",
        content=[{"id": "one", "type": "markdown", "content": "private body"}],
        media_urls={"inner": {"original": "/media/private.webp"}},
    )
    terms = [
        Taxonomy(id=uuid.uuid4(), kind=kind, name=f"Private {kind}", slug=f"{kind}-{suffix}")
        for kind in ("category", "tag")
    ]
    # The reader policy performs its preliminary checks using a separate database session.
    database = Database(isolated_settings.database.url.get_secret_value())
    try:
        async with database.session_factory.begin() as session:
            session.add_all([article, *terms])
            await session.flush()
            session.add_all(
                [ContentTaxonomy(content_id=article.id, taxonomy_id=term.id) for term in terms]
            )
        try:
            yield article, terms, password, suffix
        finally:
            async with database.session_factory.begin() as session:
                await session.execute(delete(Content).where(Content.id == article.id))
                await session.execute(
                    delete(Taxonomy).where(Taxonomy.id.in_([t.id for t in terms]))
                )
                keys = [f"content-access:{suffix}", f"content-access:{suffix}:{article.id}"]
                await session.execute(
                    delete(AuthRateLimit).where(
                        AuthRateLimit.key_hash.in_(
                            [sha256(key.encode()).hexdigest() for key in keys]
                        )
                    )
                )
    finally:
        await database.close()


async def test_locked_article_cover_and_unlocked_taxonomies(
    locked_article: tuple[Content, list[Taxonomy], str, str],
    isolated_settings: Settings,
) -> None:
    article, terms, password, suffix = locked_article
    origin = "http://127.0.0.1:3000"
    app = create_app(
        isolated_settings.model_copy(update={"cors": CORSConfig(allowed_origins=[origin])})
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, client=(suffix, 50000)),
            base_url="http://testserver",
            headers={"Origin": origin},
        ) as client,
    ):
        listing = await client.get("/v1/contents/resolve", params={"path": article.path})
        assert listing.status_code == 200, listing.text
        locked = listing.json()["data"]
        assert locked["access"] == "password" and locked["cover_url"] == article.cover_url
        assert not locked["content"] and not locked["media_urls"] and not locked["excerpt"]
        assert not locked["categories"] and not locked["tags"]
        denied = await client.post("/v1/reader/access", json={"id": str(article.id)})
        assert denied.status_code == 403, denied.text
        unlocked = await client.post(
            "/v1/reader/access", json={"id": str(article.id), "password": password}
        )
        assert unlocked.status_code == 200, unlocked.text
        assert "no-store" in unlocked.headers["cache-control"]
        detail = unlocked.json()["data"]
        assert detail["content"] == article.content
        assert detail["media_urls"]["inner"]["original"] == "/media/private.webp"
        assert [(term["slug"], term["count"]) for term in detail["categories"]] == [
            (terms[0].slug, 0)
        ]
        assert [(term["slug"], term["count"]) for term in detail["tags"]] == [(terms[1].slug, 0)]
        taxonomies = await client.get("/v1/taxonomies")
        assert taxonomies.status_code == 200, taxonomies.text
        assert not {term.slug for term in terms} & {
            term["slug"] for term in taxonomies.json()["data"]["items"]
        }


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
