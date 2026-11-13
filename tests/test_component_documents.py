"""Component drafts, public projection, and template parameter inheritance."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete

from app.core.database import Database
from app.modules.content.collaboration.models import (
    CollaborationDocument,
    CollaborationEpoch,
    CollaborationTicket,
)
from app.modules.content.component_document import ComponentDocument
from app.modules.content.draft_models import (
    ContentDraft,
    DraftReceipt,
    DraftRevision,
    TranslationGroup,
)
from app.modules.content.draft_schemas import DraftFields
from app.modules.content.models import Content
from app.modules.shortcodes.regions import ShortcodeRegion
from tests.test_accounts import AccountsTest, headers
from tests.test_accounts import accounts as accounts_fixture
from tests.test_managed_pages import body, published
from tests.test_managed_pages import pages as pages_fixture

accounts = accounts_fixture
pages = pages_fixture


def document(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "nodes": nodes}


def node(identity: str, kind: str, **props: Any) -> dict[str, Any]:
    return {"id": identity, "type": kind, "props": props}


def test_document_bounds_and_parameter_defaults() -> None:
    binding = {
        "id": "visible-title",
        "label": "标题",
        "keep_default": False,
    }
    heading = node("heading", "core.heading", text="Private template example", icon=None)
    heading["parameters"] = {"text": binding, "icon": {**binding, "id": "visible-icon"}}
    annotation = node("note", "core.comment", text="Private author annotation")
    tree = ComponentDocument.model_validate(document(heading, annotation))
    assert "Private author annotation" not in tree.public_document().text()
    assert tree.public_document({}).nodes[0].props == {}
    assert tree.public_document({"visible-title": "First", "visible-icon": None}).nodes[
        0
    ].props == {
        "text": "First",
        "icon": None,
    }
    assert tree.public_document({"visible-title": False}).nodes[0].props == {}
    assert tree.public_document().nodes[0].parameters == {}
    with pytest.raises(ValidationError, match="重复"):
        ComponentDocument.model_validate(document(heading, heading))
    with pytest.raises(ValidationError, match="version"):
        ComponentDocument.model_validate({"nodes": []})
    with pytest.raises(ValidationError):
        DraftFields(body=[])
    assert DraftFields(body=[], edit_mode="components", component_document=tree).body == []


def test_template_update_uses_applied_parameters_and_per_page_layout(pages: AccountsTest) -> None:
    def post(suffix: str, value: dict[str, Any], status: int = 200) -> dict[str, Any]:
        response = pages.root.post(
            "/v1/admin/pages" + suffix, json=value, headers=headers(pages.root)
        )
        assert response.status_code == status, response.text
        return dict(response.json()["data"])

    title = node("heading", "core.heading", text="Template example")
    title["parameters"] = {
        "text": {"id": "public-title", "label": "公开标题", "keep_default": False}
    }
    template: dict[str, Any] = {
        "id": str(uuid4()),
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "value": {
            "title": pages.name + " components",
            "edit_mode": "components",
            "layout": "sidebar",
            "component_document": document(title),
        },
    }
    saved = post("/templates", template)
    first = {
        **body(
            pages, enabled=True, layout="wide", component_parameters={"public-title": "Published"}
        ),
        "type_id": saved["type_id"],
        "apply": True,
    }
    second = {
        **body(pages, path=f"/{pages.name}-empty", enabled=True),
        "type_id": saved["type_id"],
        "apply": True,
    }
    one, two = post("/save", first), post("/save", second)
    assert one["value"].get("layout", "wide") == "wide"
    assert two["value"]["layout"] == "sidebar"
    post(
        "/save",
        {
            **first,
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "apply": False,
            "value": {**first["value"], "component_parameters": {"public-title": "Unapplied"}},
        },
    )
    updated = node("extra", "core.markdown", source="New template paragraph")
    template["value"]["component_document"] = document(title, updated)
    post("/templates", {**template, "operation_id": str(uuid4()), "expected_revision": 1})
    visible = {page["id"]: page for page in published(pages)}
    first_document = visible[one["id"]]["component_document"]
    second_document = visible[two["id"]]["component_document"]
    assert visible[one["id"]].get("layout", "wide") == "wide"
    assert visible[two["id"]]["layout"] == "sidebar"
    assert "component_parameters" not in visible[one["id"]]
    assert first_document["nodes"][0]["props"]["text"] == "Published"
    assert "text" not in second_document["nodes"][0]["props"]
    assert all(
        row["nodes"][1]["props"]["source"] == "New template paragraph"
        for row in (
            first_document,
            second_document,
        )
    )


def test_components_use_revisioned_writes_and_disable_block_collaboration(
    accounts: AccountsTest,
) -> None:
    draft_id: UUID | None = None

    def post(action: str, command: dict[str, Any], status: int = 200) -> dict[str, Any]:
        response = accounts.root.post(
            "/v1/admin/content/" + action, json=command, headers=headers(accounts.root)
        )
        assert response.status_code == status, response.text
        return dict(response.json())

    def cleanup(identity: UUID) -> None:
        async def remove() -> None:
            db = Database(accounts.settings.database.url.get_secret_value())
            try:
                async with db.session_factory() as session:
                    await session.execute(delete(Content).where(Content.id == identity))
                    await session.execute(
                        delete(ShortcodeRegion).where(ShortcodeRegion.owner_id == identity)
                    )
                    for table in (
                        CollaborationTicket,
                        CollaborationEpoch,
                        CollaborationDocument,
                        DraftReceipt,
                        DraftRevision,
                    ):
                        await session.execute(delete(table).where(table.draft_id == identity))
                    await session.execute(delete(ContentDraft).where(ContentDraft.id == identity))
                    await session.execute(
                        delete(TranslationGroup).where(TranslationGroup.id == identity)
                    )
                    await session.commit()
            finally:
                await db.close()

        asyncio.run(remove())

    original_fields = {
        "title": accounts.name,
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "Original legacy block"}],
    }
    change = {
        "id": "",
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "fields": {
            "title": accounts.name,
            "body": [],
            "edit_mode": "components",
            "component_document": document(
                node(
                    "public",
                    "core.markdown",
                    source=(
                        "Visible component phrase\n\n"
                        "{{< reply-only >}}Hidden reader-only prose{{< /reply-only >}}"
                    ),
                ),
                node("editor", "core.comment", text="Hidden editor-only note"),
            ),
        },
    }
    try:
        first = post(
            "create",
            {"type_id": "article", "operation_id": str(uuid4()), "fields": original_fields},
        )["data"]
        draft_id = UUID(first["id"])
        change["id"] = first["id"]
        post("collaboration/ticket", {"id": first["id"]})
        post("components/save", change, 409)
        detail = post("detail", {"id": first["id"]})["data"]
        change["expected_collaboration_seq"] = detail["collaboration_seq"]
        result = post("components/save", change)["data"]
        assert result["edit_mode"] == "components"
        assert result["body"] == original_fields["body"]
        assert post("components/save", change)["data"] == result
        post("collaboration/ticket", {"id": first["id"]}, 409)
        denied = {
            "id": first["id"],
            "expected_revision": 2,
            "operation_id": str(uuid4()),
            "fields": original_fields,
        }
        post("save", denied, 409)
        visible = post(
            "components/publish", {**change, "expected_revision": 2, "operation_id": str(uuid4())}
        )["data"]
        page = accounts.client().get(
            "/v1/contents/resolve", params={"path": visible["public_path"]}
        )
        assert page.status_code == 200, page.text
        live = page.json()["data"]
        assert live["content"] == []
        assert [item["id"] for item in live["component_document"]["nodes"]] == ["public"]
        assert "Visible component phrase" in live["excerpt"]
        assert "Hidden reader-only prose" not in page.text
        assert 'region="' in live["component_document"]["nodes"][0]["props"]["source"]
        matches = accounts.client().get("/v1/contents", params={"q": "Visible component phrase"})
        assert matches.status_code == 200, matches.text
        assert first["id"] in {item["id"] for item in matches.json()["data"]["items"]}
        hidden = accounts.client().get("/v1/contents", params={"q": "Hidden reader-only prose"})
        assert hidden.status_code == 200, hidden.text
        assert first["id"] not in {item["id"] for item in hidden.json()["data"]["items"]}
        restored = post(
            "components/save", {**denied, "expected_revision": 3, "operation_id": str(uuid4())}
        )["data"]
        assert restored.get("edit_mode", "blocks") == "blocks"
        assert restored["component_document"] == result["component_document"]
        post("collaboration/ticket", {"id": first["id"]})
    finally:
        if draft_id is not None:
            cleanup(draft_id)
