"""Activation warnings inspect persisted use without executing or exposing private content."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import Database
from app.modules.auth.models import User
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.models import Content
from app.modules.pages.models import ManagedPage
from app.modules.settings.extension_usage import scan_usage, shortcode_references
from app.modules.settings.extensions_schemas import ExtensionUsageQuery, InstalledPlugin
from tests.test_accounts import AccountsTest, create_user, headers, signed_in
from tests.test_accounts import accounts as accounts_fixture

accounts = accounts_fixture


def test_native_shortcode_use_ignores_source_examples_and_quoted_attributes() -> None:
    markdown = r"""{{< gallery id="1" >}} {{< gallery id="2" >}}
{{< notice >}} text {{< app:card value="inside {{< fake >}}" >}} {{< /notice >}}
\{{< escaped >}}
`{{< inline >}}` and ``{{< another >}}``
~~~html
{{< fenced >}}
~~~
> ```
> {{< quoted-fence >}}
> ```
    {{< indented >}}
<!-- {{< commented >}} -->
<pre>{{< preformatted >}}</pre><code>{{< code-example >}}</code>
<span title="{{< attribute >}}">{{< live >}}</span>
[legacy]text[/legacy]
"""
    blocks = [
        {"id": "md", "type": "markdown", "content": markdown},
        {"id": "html", "type": "html", "content": "<div>{{< video >}}</div>"},
        {"type": "markdown", "content": "{{< broken"},
        {"type": "markdown", "content": "```\n{{< unclosed-fence >}}"},
    ]
    assert shortcode_references(blocks) == {"gallery", "notice", "app:card", "live", "video"}
    assert blocks[0]["content"] == markdown
    assert shortcode_references(None) == set()


@pytest.mark.parametrize("ids", [[], ["bad"], ["../bad.plugin"], ["ok.plugin"] * 101])
def test_usage_query_validates_bounded_plugin_identifiers(ids: list[str]) -> None:
    with pytest.raises(ValidationError):
        ExtensionUsageQuery(plugin_ids=ids)


def test_usage_scans_applied_pages_and_frozen_schedules_once_per_content(
    accounts: AccountsTest,
) -> None:
    async def run() -> None:
        database = Database(accounts.settings.database.url.get_secret_value())
        identity = "usage.p" + uuid4().hex
        names = {key: "usage-" + uuid4().hex for key in ("live", "frozen", "working", "gone")}
        definition = InstalledPlugin(
            id=identity,
            title="Test plugin",
            build="a" * 64,
            page_ids=[identity, identity + ".empty", identity + ".deleted"],
            shortcodes=list(names.values()),
        )
        now = datetime.now(UTC)

        def body(name: str) -> list[dict[str, str]]:
            return [{"type": "markdown", "content": "{{< " + names[name] + " >}}"}]

        try:
            async with database.session_factory() as session:
                actor = await session.scalar(select(User.id).where(User.username == accounts.name))
                assert actor is not None
                for suffix, applied, deleted in (
                    ("", True, False),
                    (".empty", False, False),
                    (".deleted", True, True),
                ):
                    session.add(
                        ManagedPage(
                            id=uuid4(),
                            source="registered",
                            implementation_id=identity + suffix,
                            data={"title": "Private title"},
                            applied_snapshot={} if applied else None,
                            deleted_at=now if deleted else None,
                        )
                    )
                for kind, status, name in (
                    ("article", "published", "live"),
                    ("shuoshuo", "published", "live"),
                    ("article", "withdrawn", "gone"),
                ):
                    session.add(
                        Content(
                            id=uuid4(),
                            source="test",
                            kind=kind,
                            path="/" + uuid4().hex,
                            title="Private title",
                            author="Private author",
                            updated_at=now,
                            published_at=now,
                            publication_status=status,
                            content=body(name) * 2,
                        )
                    )
                draft_id, group_id = uuid4(), uuid4()
                session.add(TranslationGroup(id=group_id, source_id=draft_id, type_id="article"))
                session.add(
                    ContentDraft(
                        id=draft_id,
                        type_id="article",
                        translation_group_id=group_id,
                        created_by=actor,
                        title="Private draft",
                        excerpt="",
                        author="Private author",
                        body=body("working"),
                        revision=2,
                        created_at=now,
                        updated_at=now,
                        scheduled_for=now + timedelta(days=1),
                        scheduled_revision=1,
                        scheduled_payload={"draft": {"body": body("frozen") * 2}},
                    )
                )
                await session.flush()
                result = await scan_usage(
                    session, [definition, definition], [identity, identity, "unknown.plugin"]
                )
                used = next(plugin for plugin in result.plugins if plugin.plugin_id == identity)
                assert (used.applied_pages, used.published_contents, used.scheduled_contents) == (
                    1,
                    2,
                    1,
                )
                assert used.registered
                unknown = next(
                    plugin for plugin in result.plugins if plugin.plugin_id == "unknown.plugin"
                )
                assert not unknown.registered and unknown.applied_pages == 0
                assert "Private" not in result.model_dump_json()
                # Frozen scheduled content, rather than a newer unscheduled working version.
                definition.shortcodes = [names["working"], names["gone"]]
                unused = (await scan_usage(session, [definition], [identity])).plugins[0]
                assert unused.published_contents == unused.scheduled_contents == 0
                await session.rollback()
        finally:
            await database.close()

    asyncio.run(run())


def test_usage_endpoint_requires_settings_write_and_csrf(accounts: AccountsTest) -> None:
    body = {"plugin_ids": ["unknown.plugin"]}
    assert accounts.root.post("/v1/admin/extensions/usage", json=body).status_code == 403
    checked = accounts.root.post(
        "/v1/admin/extensions/usage", json=body, headers=headers(accounts.root)
    )
    assert checked.status_code == 200, checked.text
    assert "no-store" in checked.headers["cache-control"]
    assert checked.json()["data"]["plugins"][0]["registered"] is False
    member = create_user(accounts)
    client = accounts.client()
    signed_in(client, member["username"], "Account-temp-12345")
    assert (
        client.post("/v1/admin/extensions/usage", json=body, headers=headers(client)).status_code
        == 403
    )
