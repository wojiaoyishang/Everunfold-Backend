"""Executable rules are whitelisted by surface and are writable only by browser admins."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.page_injection_schemas import (
    PLUGIN_ID,
    STORE_KEY,
    PageInjection,
    PageInjectionState,
)
from app.modules.settings.page_injections import effective_rules, require_code_admin
from app.modules.settings.schemas import MenuItem


def test_private_rules_never_join_public_settings():
    rules = [
        PageInjection(
            id="workspace",
            name="Workspace",
            enabled=True,
            scope="admin",
            javascript="private marker",
        ),
        PageInjection(id="account", name="Account", enabled=True, scope="all", page_type="account"),
        PageInjection(
            id="article", name="Articles", enabled=True, scope="all", page_type="article"
        ),
        PageInjection(id="all", name="Everything", enabled=True, scope="all"),
        PageInjection(id="off", name="Disabled", enabled=False, scope="all"),
    ]
    data = {
        "extensions": {"enabled_plugins": [PLUGIN_ID]},
        STORE_KEY: PageInjectionState(revision=2, rules=rules).model_dump(mode="json"),
    }
    settings = public_settings(data)
    assert [rule.id for rule in settings.page_injections] == ["article", "all"]
    assert "private marker" not in settings.model_dump_json()
    assert [rule.id for rule in effective_rules(data, "admin").rules] == ["workspace", "all"]
    assert [rule.id for rule in effective_rules(data, "account").rules] == ["account", "all"]
    assert stored_settings(data, settings)[STORE_KEY] == data[STORE_KEY]
    data["extensions"] = {"enabled_plugins": []}
    assert not public_settings(data).page_injections
    assert not effective_rules(data, "admin").rules


@pytest.mark.parametrize(("role", "method"), [("member", "session"), ("admin", "api_key")])
def test_page_code_cannot_be_changed_by_granted_or_api_key_users(role, method):
    actor = Actor(uuid4(), "fixture", "Fixture", role, {"settings:write": "all"}, method)
    with pytest.raises(AuthFailure) as error:
        require_code_admin(actor)
    assert error.value.status == 403


def test_rule_and_menu_validation_preserve_legacy_settings():
    with pytest.raises(ValidationError):
        PageInjection(id="rule", name="Example", enabled=True, scope="admin", path="//example.test")
    with pytest.raises(ValidationError):
        PageInjection(id="rule", name="Example", enabled=True, scope="admin", page_type="article")
    item = MenuItem(
        id="link",
        label="Link",
        href="/",
        extra_class="my-menu   featured my-menu",
        extra_css=":scope { color: red; }",
    )
    assert item.extra_class == "my-menu featured"
    assert MenuItem(id="link", label="Link", href="/").model_dump().get("extra_class") is None
    with pytest.raises(ValidationError):
        MenuItem(id="link", label="Link", href="/", extra_class=".not-a-class")
