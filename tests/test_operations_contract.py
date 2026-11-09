import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.auth.business_auth import route_permission
from app.modules.comments.schemas import ModerateComments
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.schemas import SearchDiscovery
from tools.export_operations_contract import build_schema


def test_operations_contract_and_permissions() -> None:
    assert json.loads(Path("contracts/operations-v1.schema.json").read_text()) == build_schema()
    assert route_permission("/v1/admin/comments/query", "POST", {}) == ("comments:read", True)
    assert route_permission("/v1/admin/comments/moderate", "POST", {}) == (
        "comments:moderate",
        False,
    )
    with pytest.raises(ValidationError):
        ModerateComments.model_validate(
            {"operation_id": "00000000-0000-4000-8000-000000000001", "action": "trash", "items": []}
        )


def test_discovery_round_trip_and_validation() -> None:
    original = {"name": "Site", "author": "Author", "description": ""}
    settings = public_settings(original)
    assert settings.site.discovery == SearchDiscovery()
    settings.site.discovery = SearchDiscovery(feeds_enabled=False, google_verification="abc-123")
    assert public_settings(stored_settings(original, settings)) == settings
    for value in [
        {"feed_limit": 101},
        {"google_verification": '<meta name="google"/>'},
        {"share_image": "javascript:alert(1)"},
    ]:
        with pytest.raises(ValidationError):
            SearchDiscovery.model_validate(value)
