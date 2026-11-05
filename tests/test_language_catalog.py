"""Configurable content languages do not inherit the installed UI pack limit."""

import pytest
from pydantic import ValidationError

from app.modules.settings.languages import (
    LanguageCatalog,
    LanguageDefinition,
    preferred_language,
    reserved_language_path,
)


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
    assert LanguageDefinition(code="fr-CA", name="Français", prefix="/fr-ca").flag == "🇨🇦"
    assert LanguageDefinition(code="ja", name="日本語", prefix="/ja", flag="⭐").flag == "⭐"
    assert (
        LanguageDefinition(code="en", name="English", prefix="/en", ui_locale="zh-CN").ui_locale
        == "en"
    )
    assert (
        LanguageDefinition(code="ja", name="日本語", prefix="/ja", ui_locale="zh-CN").ui_locale
        == "zh-CN"
    )
    configured = LanguageCatalog(
        languages=[
            LanguageDefinition(code="zh-CN", name="简体中文", prefix="/zh"),
            LanguageDefinition(code="en", name="English", prefix="/en"),
        ]
    )
    assert configured.languages[0].prefix == "/zh"
    assert preferred_language(catalog, "en-x-37;q=0.9,zh-CN;q=0.5") == "en-x-37"
    assert reserved_language_path("/language-37/search", catalog)
    assert reserved_language_path("/language-37", catalog)
    assert reserved_language_path("/language-37/category/example", catalog)
    assert not reserved_language_path("/language-37/story.html", catalog)


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
