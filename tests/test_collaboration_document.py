"""Wire/projection invariants, including unresolved Yrs dependencies."""

from uuid import UUID, uuid4

import pytest
from pycrdt import Map
from pydantic import ValidationError

from app.modules.content.collaboration.document import decode, project, seed
from app.modules.content.collaboration.service import apply_candidate
from app.modules.content.draft_schemas import DraftBlock, DraftFields
from app.modules.content.editor_schema import EditorSettings


def test_crdt_projection_reorder_and_tombstone() -> None:
    one, two = str(uuid4()), str(uuid4())
    fields = DraftFields(
        title="中文🌟",
        body=[
            DraftBlock(id=UUID(one), content="a"),
            DraftBlock(id=UUID(two), type="html", content="<p>b</p>"),
        ],
    )
    doc = seed(fields)
    blocks = doc.get("blocks", type=Map)
    original = blocks[one]["content"]
    with doc.transaction():
        blocks[one]["deleted"] = True
        blocks[two]["content"].insert(3, "你好👨‍👩‍👧")
    result = project(decode(doc.get_update()))
    assert [str(part.id) for part in result.body] == [two]
    assert "你好👨‍👩‍👧" in result.body[0].content
    assert str(original) == "a"


def test_forbidden_policy_and_missing_dependencies_are_not_acknowledgeable() -> None:
    fields = DraftFields(body=[DraftBlock(id=uuid4(), content="")])
    doc = seed(fields)
    initial = doc.get_update()
    vector = doc.get_state()
    text = doc.get("blocks", type=Map)[str(fields.body[0].id)]["content"]
    text.insert(0, "A")
    first = doc.get_update(vector)
    vector = doc.get_state()
    text.insert(1, "B")
    second = doc.get_update(vector)
    with pytest.raises(ValueError):
        apply_candidate(initial, second, EditorSettings())
    state, _ = apply_candidate(initial, first, EditorSettings())
    _, result = apply_candidate(state, second, EditorSettings())
    assert result.body[0].content == "AB"
    # Deletions also require their referenced structs to be integrated before ACK.
    vector = doc.get_state()
    del text[0:2]
    deletion = doc.get_update(vector)
    with pytest.raises(ValueError):
        apply_candidate(initial, deletion, EditorSettings())
    doc.get("meta", type=Map)["settings"]["visibility"] = "public"
    with pytest.raises(ValueError):
        project(doc)


def test_upload_placeholders_cannot_be_saved_as_formal_content() -> None:
    from app.modules.content.collaboration.schemas import BlockSourcePatch

    marker = '<img src="everunfold-upload://temporary" alt="uploading">'
    with pytest.raises(ValidationError):
        DraftFields(body=[DraftBlock(id=uuid4(), type="html", content=marker)])
    with pytest.raises(ValidationError):
        BlockSourcePatch(id=uuid4(), type="html", base_hash="a" * 64, content=marker)
    DraftFields(
        body=[
            DraftBlock(
                id=uuid4(),
                type="html",
                content='&lt;img src="everunfold-upload://example"&gt;',
            )
        ]
    )
