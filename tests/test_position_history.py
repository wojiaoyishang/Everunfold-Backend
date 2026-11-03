"""Actual y-tiptap history shape is private, bounded, append-only and non-business."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from pycrdt import Doc, Map

from app.modules.content.collaboration.document import decode, project, seed
from app.modules.content.collaboration.position_history import position_history, require_append_only
from app.modules.content.draft_schemas import DraftBlock, DraftFields

NAME = "__tiptap_position_migrations:item:10:20"
RECORD: dict[str, Any] = {
    "version": 1,
    "client": 10,
    "clock": 30,
    "seen": [[10, 30]],
    "transfers": [
        {"source": {"client": 10, "clock": 21, "length": 1}, "target": None},
        {
            "source": {"client": 10, "clock": 22, "length": 1, "boundary": "start"},
            "target": {"client": 11, "clock": 2, "type": {"client": 11, "clock": 1}, "assoc": -1},
        },
    ],
}


def document() -> Doc[Any]:
    doc = seed(
        DraftFields(
            title="unchanged", body=[DraftBlock(id=uuid4(), type="html", content="<p>正文</p>")]
        )
    )
    doc.get(NAME, type=Map)["10:30"] = deepcopy(RECORD)
    return decode(doc.get_update())


def test_engine_position_metadata_round_trips_without_entering_business_projection() -> None:
    doc = document()
    assert position_history(doc)[NAME]["10:30"]["version"] == 1
    fields = project(doc)
    assert fields.title == "unchanged"
    assert fields.body[0].content == "<p>正文</p>"
    assert "tiptap" not in fields.model_dump_json()


@pytest.mark.parametrize(
    "root", ["account", "__tiptap_position_migrations:root:meta", NAME + ":other"]
)
def test_arbitrary_or_business_roots_remain_rejected(root: str) -> None:
    doc = document()
    doc.get(root, type=Map)["role"] = "admin"
    with pytest.raises(ValueError):
        project(doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("client", True),
        ("clock", 30.5),
        ("seen", "unsafe"),
        ("transfers", [{}]),
        ("access_password", "forbidden"),
    ],
)
def test_malformed_or_extra_position_fields_are_rejected(field: str, value: Any) -> None:
    doc = document()
    record = deepcopy(RECORD)
    record[field] = value
    doc.get(NAME, type=Map)["10:30"] = record
    with pytest.raises(ValueError):
        project(doc)


def test_committed_history_cannot_be_rewritten_or_deleted_but_can_grow() -> None:
    doc = document()
    before = position_history(doc)
    added = {**RECORD, "clock": 31}
    doc.get(NAME, type=Map)["10:31"] = added
    require_append_only(before, decode(doc.get_update()))
    doc.get(NAME, type=Map)["10:30"] = {**RECORD, "transfers": []}
    with pytest.raises(ValueError, match="append-only"):
        require_append_only(before, doc)
    del doc.get(NAME, type=Map)["10:30"]
    with pytest.raises(ValueError, match="append-only"):
        require_append_only(before, doc)
