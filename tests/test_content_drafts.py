"""Registration, public/private contract separation and bounded stable block identity."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.content.draft_schemas import DraftBlock, DraftFields
from app.modules.content.type_registry import ContentTypeRegistry, content_types
from tools.export_content_admin_contract import build_schema


def test_content_registration_and_authoritative_contract() -> None:
    definitions = content_types.all()
    assert [item.id for item in definitions] == ["article", "shuoshuo"]
    assert definitions[0].title_required and not definitions[1].title_required
    assert content_types.get("page") is None
    with pytest.raises(ValueError, match="Duplicate"):
        ContentTypeRegistry([definitions[0], definitions[0]])
    custom = definitions[0].model_copy(update={"id": "journal", "owner": "test"})
    assert ContentTypeRegistry([*definitions, custom]).get("journal") == custom
    source = Path(__file__).resolve().parents[1] / "contracts/content-admin-v1.schema.json"
    assert json.loads(source.read_text(encoding="utf-8")) == build_schema()


def test_stable_unique_blocks_and_payload_bounds() -> None:
    block = DraftBlock(id=uuid4(), content="正文")
    assert DraftFields(body=[block]).body[0].id == block.id
    with pytest.raises(ValidationError):
        DraftFields(body=[block, block])
    with pytest.raises(ValidationError):
        DraftFields(body=[])
    with pytest.raises(ValidationError):
        DraftFields(body=[DraftBlock(id=uuid4(), content="x" * 80_000) for _ in range(3)])
