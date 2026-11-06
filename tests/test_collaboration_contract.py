"""Prevent live draft DTO changes from silently invalidating commit receipts in clients."""

import json
from pathlib import Path

from tools.export_collaboration_contract import build_schema


def test_collaboration_contract_matches_live_draft_projection() -> None:
    schema = build_schema()
    published = json.loads(Path("contracts/collaboration-v1.schema.json").read_text())
    assert published == schema
    owner = schema["$defs"]["DraftDetail"]["properties"]["owner_id"]
    assert {"type": "string", "format": "uuid"} in owner["anyOf"]
