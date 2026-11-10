"""Glossary matching stays article-scoped and independent from cached content bodies."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.database import Database
from app.modules.content.models import Content
from app.modules.settings.backend_plugins import install_backend_plugins
from app.plugins.glossary.schemas import GlossaryBlock, GlossaryFields
from app.plugins.glossary.service import (
    operation_fingerprint,
    searchable_source,
    target_in_source,
)
from tests.test_accounts import AccountsTest, headers
from tests.test_page_comments import comments as glossary_database  # noqa: F401


def test_glossary_text_matching_ignores_code_links_and_markup() -> None:
    source = searchable_source(
        [
            {
                "id": "one",
                "type": "markdown",
                "content": "正文出现金灵。`金灵` 和 [金灵](https://example.test) 不参与。\n"
                "```\n金灵\n```\n<strong>金灵</strong>",
            }
        ]
    )
    assert source.count("金灵") == 2
    assert target_in_source(source, "金灵")
    assert target_in_source("uses json safely", "JSON")
    assert not target_in_source("uses json safely", "JS")
    assert (
        GlossaryFields(
            slug="all-languages",
            title="通用术语",
            summary="默认对所有文章语言生效。",
            body=[
                GlossaryBlock(
                    id=uuid4(),
                    type="markdown",
                    content="通用说明。",
                )
            ],
        ).language
        == "*"
    )
    assert operation_fingerprint({"b": 2, "a": 1}) == operation_fingerprint({"a": 1, "b": 2})


def test_glossary_admin_and_article_scoped_public_matching(
    request: pytest.FixtureRequest,
) -> None:
    ctx: AccountsTest = request.getfixturevalue("glossary_database")

    async def deploy() -> tuple[str, str]:
        database = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                await install_backend_plugins(session, ctx.settings)
                now = datetime.now(UTC)
                matched = Content(
                    id=uuid4(),
                    source="test",
                    kind="article",
                    title="出现金灵的文章",
                    path="/glossary-hit",
                    excerpt="",
                    language="zh-CN",
                    published_at=now,
                    updated_at=now,
                    author="Test",
                    publication_status="published",
                    visibility="public",
                    content=[
                        {
                            "id": "body",
                            "type": "markdown",
                            "content": "今天在操场遇见了金灵，她正在看云。",
                        }
                    ],
                )
                unmatched = Content(
                    id=uuid4(),
                    source="test",
                    kind="article",
                    title="没有术语的文章",
                    path="/glossary-miss",
                    excerpt="",
                    language="zh-CN",
                    published_at=now,
                    updated_at=now,
                    author="Test",
                    publication_status="published",
                    visibility="public",
                    content=[{"id": "body", "type": "markdown", "content": "今天只有风和树叶。"}],
                )
                session.add_all([matched, unmatched])
                await session.commit()
                return str(matched.id), str(unmatched.id)
        finally:
            await database.close()

    matched, unmatched = asyncio.run(deploy())
    base = "/v1/plugins/everunfold.glossary"
    create = ctx.root.post(
        base + "/admin/terms",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "term_id": str(uuid4()),
            "fields": {
                "language": "*",
                "slug": "jin-ling",
                "title": "金灵",
                "summary": "一位喜欢看云的旧同学。",
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "她出现在**旧日校园回忆**中。",
                    }
                ],
                "aliases": ["金灵同学"],
                "status": "published",
            },
        },
    )
    assert create.status_code == 200, create.text
    term_id = create.json()["data"]["id"]
    conflict = ctx.root.post(
        base + "/admin/terms",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "term_id": str(uuid4()),
            "fields": {
                "language": "en",
                "slug": "another",
                "title": "别名冲突",
                "summary": "不应创建",
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "",
                    }
                ],
                "aliases": ["金灵同学"],
                "status": "published",
            },
        },
    )
    assert conflict.status_code == 409
    public = ctx.client()
    hit = public.get(base + f"/for-content/{matched}")
    miss = public.get(base + f"/for-content/{unmatched}")
    assert hit.status_code == 200, hit.text
    assert [item["title"] for item in hit.json()["data"]["terms"]] == ["金灵"]
    assert hit.json()["data"]["terms"][0]["body"][0]["content"] == "她出现在**旧日校园回忆**中。"
    detail = public.get(base + "/terms/jin-ling?language=zh-CN")
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["body"][0]["type"] == "markdown"
    assert [
        item["title"] for item in public.get(base + "/index?language=en").json()["data"]["terms"]
    ] == ["金灵"]
    assert miss.status_code == 200
    assert miss.json()["data"]["terms"] == []
    update = ctx.root.patch(
        base + f"/admin/terms/{term_id}",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "fields": {
                "language": "*",
                "slug": "jin-ling",
                "title": "金灵",
                "summary": "更新后的提示。",
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "更新后的 **说明**。",
                    }
                ],
                "aliases": ["金灵同学"],
                "status": "published",
            },
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["revision"] == 2
    stale = ctx.root.patch(
        base + f"/admin/terms/{term_id}",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "fields": {
                "language": "*",
                "slug": "jin-ling",
                "title": "金灵",
                "summary": "争执后的提示。",
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "争执后的说明。",
                    }
                ],
                "aliases": ["金灵同学"],
                "status": "draft",
            },
        },
    )
    assert stale.status_code == 409
    archived = ctx.root.patch(
        base + f"/admin/terms/{term_id}",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 2,
            "fields": {
                "language": "*",
                "slug": "jin-ling",
                "title": "金灵",
                "summary": "归档后不再提示。",
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "归档词条保留记录。",
                    }
                ],
                "aliases": ["金灵同学"],
                "status": "archived",
            },
        },
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["data"]["status"] == "archived"
    assert public.get(base + f"/for-content/{matched}").json()["data"]["terms"] == []
    assert public.get(base + "/index?language=zh-CN").json()["data"]["terms"] == []
