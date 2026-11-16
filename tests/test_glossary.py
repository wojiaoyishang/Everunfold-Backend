"""Glossary matching stays article-scoped and independent from cached content bodies."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.database import Database
from app.modules.auth.security import hash_password
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.models import Content
from app.modules.pages.schemas import PageValue
from app.modules.settings.backend_plugins import install_backend_plugins
from app.modules.settings.schemas import GlossaryPreferences
from app.plugins.glossary.matching import MatchRule, evaluate_rules
from app.plugins.glossary.models import GlossaryHook
from app.plugins.glossary.schemas import (
    GlossaryBlock,
    GlossaryFields,
    GlossaryHookFields,
    GlossaryMatchPreviewInput,
    GlossaryMatchRule,
)
from app.plugins.glossary.service import (
    hook_targets_surface,
    operation_fingerprint,
    preview_matches,
    searchable_source,
    target_in_source,
)
from tests.test_accounts import ORIGIN, AccountsTest, headers
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
    preferences = GlossaryPreferences()
    assert preferences.popup_source == "content"
    assert "article_footnotes" not in GlossaryPreferences.model_fields
    assert EditorSettings().show_glossary_tooltips is True
    assert EditorSettings().show_glossary_footnotes is True
    assert EditorSettings().show_image_viewer is True
    per_article = EditorSettings(
        show_image_viewer=False,
        show_glossary_tooltips=False,
        show_glossary_footnotes=False,
    )
    assert per_article.show_glossary_tooltips is False
    assert per_article.show_glossary_footnotes is False
    assert per_article.show_image_viewer is False
    page = PageValue(title="Page", path="/page")
    assert page.show_glossary_tooltips is True
    assert page.show_glossary_footnotes is True
    assert page.show_image_viewer is True
    optional_summary = GlossaryFields(
        slug="body-only",
        title="正文即提示",
        body=[GlossaryBlock(id=uuid4(), content="正文说明。")],
    )
    assert optional_summary.summary == ""
    assert optional_summary.show_image_viewer is True


def test_regex_glossary_rules_are_bounded_and_preserve_match_options() -> None:
    sample = "人工智能技术与 AI 系统；可读性不是 ai 化。"
    regex = GlossaryMatchRule(kind="regex", pattern="(?:AI|人工智能)(?:技术|系统)?")
    hits = evaluate_rules(sample, [MatchRule(**regex.model_dump())])
    assert {hit.text for hit in hits} == {"人工智能技术", "AI"}
    assert all(hit.whole_word for hit in hits)
    sensitive = evaluate_rules(
        sample,
        [MatchRule(kind="literal", pattern="AI", case_sensitive=True)],
    )
    assert [hit.text for hit in sensitive] == ["AI"]
    for expression in (r"(a+)+", r"\w+", "^", r"(.)\1", r"(?=AI)AI"):
        with pytest.raises(ValueError):
            GlossaryMatchRule(kind="regex", pattern=expression)
    fields = GlossaryFields(
        slug="configurable",
        title="标题",
        summary="说明",
        body=[GlossaryBlock(id=uuid4(), content="")],
        aliases=["旧别名"],
        auto_match_title=False,
        match_rules=[],
    )
    from app.plugins.glossary.service import term_targets

    assert term_targets(fields) == ["旧别名"]
    preview = preview_matches(
        GlossaryMatchPreviewInput(
            title="人工智能",
            aliases=["AI"],
            match_rules=[GlossaryMatchRule(kind="regex", pattern="(?:AI|人工智能)(?:技术|系统)?")],
            sample="人工智能技术和AI系统都可以检索。",
        )
    )
    assert {item.text for item in preview.hits} == {
        "人工智能",
        "人工智能技术",
        "AI",
        "AI系统",
    }
    alias_preview = preview_matches(
        GlossaryMatchPreviewInput(
            title="标题",
            aliases=["独立别名"],
            auto_match_title=False,
            match_rules=[],
            sample="正文里的独立别名仍然命中。",
        )
    )
    assert [item.text for item in alias_preview.hits] == ["独立别名"]
    title_preview = preview_matches(
        GlossaryMatchPreviewInput(
            title="默认匹配标题",
            sample="这是默认匹配标题的测试。",
        )
    )
    assert [item.text for item in title_preview.hits] == ["默认匹配标题"]


def test_cross_page_hook_selectors_and_targets_are_bounded() -> None:
    target = uuid4()
    fields = GlossaryHookFields(
        selector=".article-header .avatar",
        scope="content",
        targets=[{"kind": "article", "id": target}],
        priority=20,
    )
    assert fields.targets[0].id == target
    assert GlossaryHookFields(selector="#avatar").targets == []
    script_hook = GlossaryHookFields(
        mode="script",
        selector="",
        script='glossary.mount(document.querySelector("#avatar"));',
    )
    assert script_hook.mode == "script"
    assert "#avatar" in script_hook.script
    for selector in ("*", "body", "#avatar::before", "a{color:red}", ""):
        with pytest.raises(ValueError):
            GlossaryHookFields(selector=selector)
    with pytest.raises(ValueError):
        GlossaryHookFields(mode="script", selector="", script=" ")
    with pytest.raises(ValueError):
        GlossaryHookFields(selector="#avatar", scope="content")
    with pytest.raises(ValueError):
        GlossaryHookFields(
            selector="#avatar",
            scope="all",
            targets=[{"kind": "article", "id": target}],
        )
    hook = GlossaryHook(
        scope="content",
        targets=[{"kind": "article", "id": str(target)}],
    )
    assert hook_targets_surface(hook, "article", target, None)
    assert not hook_targets_surface(hook, "article", uuid4(), None)
    assert not hook_targets_surface(hook, "code", target, None)


@pytest.mark.parametrize("component_article", [False, True], ids=["blocks", "components"])
def test_glossary_admin_and_article_scoped_public_matching(
    request: pytest.FixtureRequest, component_article: bool
) -> None:
    ctx: AccountsTest = request.getfixturevalue("glossary_database")

    async def deploy() -> tuple[str, str, str, str]:
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
                protected = Content(
                    id=uuid4(),
                    source="test",
                    kind="article",
                    title="受限文章",
                    path="/glossary-password",
                    excerpt="",
                    language="zh-CN",
                    published_at=now,
                    updated_at=now,
                    author="Test",
                    publication_status="published",
                    visibility="password",
                    access_password_hash=hash_password("glossary-test-password"),
                    content=[] if component_article else matched.content,
                    component_document={
                        "version": 1,
                        "nodes": [
                            {
                                "id": "prose",
                                "type": "core.markdown",
                                "props": {"source": "在术语组件中出现金灵同学。"},
                            },
                        ],
                    }
                    if component_article
                    else None,
                )
                captcha_only = Content(
                    id=uuid4(),
                    source="test",
                    kind="article",
                    title="需验证的文章",
                    path="/glossary-captcha",
                    excerpt="",
                    language="zh-CN",
                    published_at=now,
                    updated_at=now,
                    author="Test",
                    publication_status="published",
                    visibility="public",
                    captcha_read_required=True,
                    content=matched.content,
                )
                session.add_all([matched, unmatched, protected, captcha_only])
                await session.commit()
                return str(matched.id), str(unmatched.id), str(protected.id), str(captcha_only.id)
        finally:
            await database.close()

    matched, unmatched, protected, captcha_only = asyncio.run(deploy())
    base = "/v1/plugins/everunfold.glossary"
    uploaded = ctx.root.post(
        "/v1/admin/media/upload",
        params={"filename": "glossary-feature.svg", "upload_id": str(uuid4())},
        content=(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
            b'<rect width="120" height="80" fill="#415fb7"/></svg>'
        ),
        headers={**headers(ctx.root), "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    feature = uploaded.json()["data"]
    feature_id = feature["id"].removeprefix("media:")
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
                "summary": "",
                "feature_media_id": feature_id,
                "share_image_media_id": feature_id,
                "seo_title": "金灵的独立搜索标题",
                "seo_max_snippet": 0,
                "allow_indexing": False,
                "body": [
                    {
                        "id": str(uuid4()),
                        "type": "markdown",
                        "content": "她出现在**旧日校园回忆**中。",
                    },
                    {
                        "id": str(uuid4()),
                        "type": "html",
                        "content": "<p><strong>HTML 正文块</strong>与 Markdown 混合保存。</p>",
                    },
                ],
                "aliases": ["金灵同学"],
                "status": "published",
            },
        },
    )
    assert create.status_code == 200, create.text
    term_id = create.json()["data"]["id"]
    preview = ctx.root.post(
        base + "/admin/preview",
        headers=headers(ctx.root),
        json={
            "title": "金灵",
            "sample": "这段正文提到了金灵，默认标题规则应该命中。",
        },
    )
    assert preview.status_code == 200, preview.text
    assert [hit["text"] for hit in preview.json()["data"]["hits"]] == ["金灵"]
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
    protected_path = base + f"/for-content/{protected}"
    for path in [protected_path, base + f"/for-content/{captcha_only}"]:
        assert public.get(path).json()["data"]["terms"] == []
    missing = public.post(protected_path)
    assert missing.status_code == 403
    assert missing.headers["Cache-Control"] == "private, no-store"
    unlocked = public.post(
        "/v1/reader/access",
        json={"id": protected, "password": "glossary-test-password"},
        headers={"Origin": ORIGIN},
    )
    assert unlocked.status_code == 200, unlocked.text
    grant = {
        "X-Everunfold-Read-Receipt": unlocked.json()["data"]["read_receipt"],
        "X-Everunfold-Read-Target": protected,
    }
    private_matches = public.post(protected_path, headers=grant)
    assert private_matches.status_code == 200, private_matches.text
    assert private_matches.headers["Cache-Control"] == "private, no-store"
    assert [item["title"] for item in private_matches.json()["data"]["terms"]] == ["金灵"]
    assert public.get(protected_path, headers=grant).json()["data"]["terms"] == []
    assert public.post(base + f"/for-content/{captcha_only}", headers=grant).status_code == 403
    assert (
        public.post(
            base + f"/for-content/{captcha_only}",
            headers={**grant, "X-Everunfold-Read-Target": captcha_only},
        ).status_code
        == 403
    )
    assert hit.json()["data"]["terms"][0]["feature_image"]["original"] == feature["url"]
    assert hit.json()["data"]["terms"][0]["body"][0]["content"] == "她出现在**旧日校园回忆**中。"
    assert [block["type"] for block in hit.json()["data"]["terms"][0]["body"]] == [
        "markdown",
        "html",
    ]
    detail = public.get(base + "/terms/jin-ling?language=zh-CN")
    assert detail.status_code == 200, detail.text
    assert [block["type"] for block in detail.json()["data"]["body"]] == ["markdown", "html"]
    assert detail.json()["data"]["feature_image"]["original"] == feature["url"]
    assert detail.json()["data"]["share_image_url"] == feature["url"]
    assert detail.json()["data"]["seo_title"] == "金灵的独立搜索标题"
    assert detail.json()["data"]["seo_max_snippet"] == 0
    assert detail.json()["data"]["allow_indexing"] is False
    assert "share_image_media_id" not in detail.json()["data"]
    assert "seo_title" not in hit.json()["data"]["terms"][0]
    admin_list = ctx.root.post(
        base + "/admin/query",
        headers=headers(ctx.root),
        json={"status": "all", "page": 1, "page_size": 30},
    )
    assert admin_list.status_code == 200, admin_list.text
    listed = next(item for item in admin_list.json()["data"]["items"] if item["id"] == term_id)
    assert listed["feature_image"]["original"] == feature["url"]
    assert [
        item["title"] for item in public.get(base + "/index?language=en").json()["data"]["terms"]
    ] == ["金灵"]
    assert (
        public.get(base + "/index?language=en").json()["data"]["terms"][0]["feature_image"][
            "original"
        ]
        == feature["url"]
    )
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
                "feature_media_id": feature_id,
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
    hook_id = uuid4()
    hook_operation = uuid4()
    hook = ctx.root.post(
        base + "/admin/hooks",
        headers=headers(ctx.root),
        json={
            "operation_id": str(hook_operation),
            "hook_id": str(hook_id),
            "term_id": term_id,
            "fields": {
                "selector": "#avatar",
                "scope": "content",
                "targets": [{"kind": "article", "id": matched}],
                "enabled": True,
                "priority": 25,
            },
        },
    )
    assert hook.status_code == 200, hook.text
    assert hook.json()["data"]["term"]["title"] == "金灵"
    replay_hook = ctx.root.post(
        base + "/admin/hooks",
        headers=headers(ctx.root),
        json={
            "operation_id": str(hook_operation),
            "hook_id": str(hook_id),
            "term_id": term_id,
            "fields": {
                "selector": "#avatar",
                "scope": "content",
                "targets": [{"kind": "article", "id": matched}],
                "enabled": True,
                "priority": 25,
            },
        },
    )
    assert replay_hook.status_code == 200
    hooks = public.get(
        base + f"/hooks/for-target?surface=article&content_id={matched}&language=zh-CN"
    )
    assert hooks.status_code == 200, hooks.text
    assert [item["selector"] for item in hooks.json()["data"]["hooks"]] == ["#avatar"]
    wrong_target = public.get(
        base + f"/hooks/for-target?surface=article&content_id={unmatched}&language=zh-CN"
    )
    assert wrong_target.json()["data"]["hooks"] == []
    disabled = ctx.root.patch(
        base + f"/admin/hooks/{hook_id}",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "term_id": term_id,
            "fields": {
                "selector": "#avatar",
                "scope": "content",
                "targets": [{"kind": "article", "id": matched}],
                "enabled": False,
                "priority": 25,
            },
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["revision"] == 2
    assert (
        public.get(
            base + f"/hooks/for-target?surface=article&content_id={matched}&language=zh-CN"
        ).json()["data"]["hooks"]
        == []
    )
    archived_hook = ctx.root.post(
        base + f"/admin/hooks/{hook_id}/archive",
        headers=headers(ctx.root),
        json={"operation_id": str(uuid4()), "expected_revision": 2},
    )
    assert archived_hook.status_code == 200, archived_hook.text
    assert archived_hook.json()["data"]["status"] == "archived"
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
