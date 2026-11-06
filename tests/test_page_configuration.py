"""Open JSON configuration stays bounded without inheriting frontend control schemas."""

import pytest
from pydantic import JsonValue, ValidationError

from app.modules.pages.schemas import PageRegistration, PageValue


def test_page_configuration_preserves_nested_values_and_arbitrary_keys() -> None:
    options: dict[str, JsonValue] = {
        "not-a-form-field": [None, False, 0, 2.5, {"中文": {"deep": [1, "two"]}}],
        "__proto__": {"safe": "plain JSON data"},
        "url": "javascript:stored-data-is-not-a-navigation-instruction",
    }
    value = PageValue(title="Config", path="/config", options=options)
    assert PageValue.model_validate_json(value.model_dump_json()).options == options
    declaration = PageRegistration(
        id="plugin.config",
        version="1.0.0",
        title="Config",
        path="/config",
        defaults=options,
        settings_schema={
            "fields": [
                {
                    "key": "extra",
                    "component": "new-plugin.custom-control",
                    "props": {"nested": [True, None]},
                }
            ]
        },
    )
    assert declaration.fields == []
    assert declaration.defaults == options
    assert declaration.settings_schema["fields"]


def test_page_configuration_rejects_nonfinite_oversized_or_excessively_nested_data() -> None:
    nested: dict[str, object] = {}
    for _ in range(34):
        nested = {"next": nested}
    for options in (
        {"value": float("inf")},
        {"value": float("nan")},
        {"large": "x" * 262144},
        nested,
    ):
        with pytest.raises(ValidationError):
            PageValue.model_validate({"title": "Config", "path": "/config", "options": options})
        with pytest.raises(ValidationError):
            PageRegistration.model_validate(
                {
                    "id": "plugin.config",
                    "version": "1.0.0",
                    "title": "Config",
                    "path": "/config",
                    "defaults": options,
                }
            )
