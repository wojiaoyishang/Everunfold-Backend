"""Prevent live draft DTO changes from silently invalidating commit receipts in clients."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.content.collaboration.schemas import CommitDraft
from tools.export_collaboration_contract import build_schema


def test_collaboration_contract_matches_live_draft_projection() -> None:
    schema = build_schema()
    published = json.loads(Path("contracts/collaboration-v1.schema.json").read_text())
    assert published == schema
    owner = schema["$defs"]["DraftDetail"]["properties"]["owner_id"]
    assert {"type": "string", "format": "uuid"} in owner["anyOf"]


def test_only_scheduled_commands_accept_timezone_aware_publication_dates() -> None:
    command = dict(
        id=uuid4(), operation_id=uuid4(), expected_revision=1, epoch=uuid4(), committed_seq=0
    )
    with pytest.raises(ValidationError):
        CommitDraft(**command, action="schedule")
    with pytest.raises(ValidationError):
        CommitDraft(**command, action="publish", scheduled_for="2030-01-01T00:00:00Z")
    with pytest.raises(ValidationError):
        CommitDraft(**command, action="schedule", scheduled_for="2030-01-01T00:00:00")
    assert CommitDraft(
        **command, action="schedule", scheduled_for="2030-01-01T00:00:00Z"
    ).scheduled_for
