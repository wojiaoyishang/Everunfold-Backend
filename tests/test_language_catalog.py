"""Configurable content languages do not inherit the installed UI pack limit."""

import pytest
from pydantic import ValidationError

from app.modules.settings.languages import LanguageCatalog, LanguageDefinition, preferred_language


def test_languages_are_open_ended_and_normalized() -> None:
    catalog = LanguageCatalog(
        languages=[
            LanguageDefinition(code="zh-CN", name="简体中文", prefix=""),
            *[
                LanguageDefinition(
                    code=f"en-x-{index}", name=f"Variant {index}", prefix=f"/language-{index}"
                )
                for index in range(48)
            ],
        ]
    )
    assert len(catalog.languages) == 49
    assert LanguageDefinition(code="FR-ca", name="Français", prefix="/fr-ca").code == "fr-CA"
    assert preferred_language(catalog, "en-x-37;q=0.9,zh-CN;q=0.5") == "en-x-37"


@pytest.mark.parametrize(
    "changes",
    [
        {"languages": [LanguageDefinition(code="en", name="English", prefix="/en")]},
        {
            "languages": [
                LanguageDefinition(code="zh-CN", name="中文", prefix=""),
                LanguageDefinition(code="zh-cn", name="Duplicate", prefix="/duplicate"),
            ]
        },
        {
            "languages": [
                LanguageDefinition(code="zh-CN", name="中文", prefix=""),
                LanguageDefinition(code="fr", name="Français", prefix=""),
            ]
        },
    ],
)
def test_language_catalog_rejects_ambiguous_identity_or_default(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LanguageCatalog.model_validate(changes)
