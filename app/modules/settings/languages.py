"""Database-owned content languages, independent of installed program dictionaries."""

from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.locales import LOCALES, Locale
from app.core.schemas import PublicModel


def canonical_language(value: str) -> str:
    parts = value.split("-")
    return "-".join(
        [
            parts[0].lower(),
            *[
                part.title()
                if len(part) == 4 and part.isalpha()
                else part.upper()
                if len(part) == 2 and part.isalpha()
                else part.lower()
                for part in parts[1:]
            ],
        ]
    )


LanguageCode = Annotated[
    str,
    StringConstraints(
        min_length=2, max_length=32, pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$"
    ),
    AfterValidator(canonical_language),
]

# Presentation defaults only; flags never identify a language or grant access.
LANGUAGE_REGIONS = {
    "zh": "CN",
    "en": "GB",
    "ja": "JP",
    "fr": "FR",
    "de": "DE",
    "es": "ES",
    "pt": "PT",
    "ru": "RU",
    "ar": "SA",
    "ko": "KR",
    "it": "IT",
    "nl": "NL",
    "hi": "IN",
    "uk": "UA",
    "vi": "VN",
    "th": "TH",
    "id": "ID",
    "tr": "TR",
    "pl": "PL",
    "fi": "FI",
    "sv": "SE",
    "nb": "NO",
    "da": "DK",
    "el": "GR",
    "he": "IL",
    "fa": "IR",
    "bn": "BD",
    "ta": "IN",
    "ms": "MY",
    "fil": "PH",
    "ur": "PK",
    "sw": "TZ",
}


def default_flag(code: str) -> str:
    parts = code.split("-")
    region = next(
        (part.upper() for part in parts[1:] if len(part) == 2 and part.isalpha()),
        LANGUAGE_REGIONS.get(parts[0].lower()),
    )
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in region) if region else "🏳️"


class LanguageDefinition(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: LanguageCode
    name: str = Field(min_length=1, max_length=80)
    flag: str = Field(default="", max_length=16)
    prefix: str = Field(default="", pattern=r"^(?:|/[a-z0-9][a-z0-9-]{0,31})$")
    enabled: bool = True
    ui_locale: Locale = "en"

    @model_validator(mode="after")
    def presentation_defaults(self) -> Self:
        if not self.flag.strip():
            self.flag = default_flag(self.code)
        if self.code in LOCALES:
            self.ui_locale = self.code
        return self

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("language name cannot be blank")
        return value.strip()

    @field_validator("prefix")
    @classmethod
    def reserved_prefix(cls, value: str) -> str:
        if value and value[1:] in {
            "admin",
            "account",
            "login",
            "api",
            "v1",
            "internal",
            "health",
            "reference",
            "media",
            "uploads",
            "archives",
            "article",
            "gzrj",
            "about",
            "categories",
            "category",
            "tag",
            "tags",
            "search",
            "ss",
            "content-features",
        }:
            raise ValueError("reserved language prefix")
        return value


def initial_languages() -> list[LanguageDefinition]:
    return [
        LanguageDefinition(code="zh-CN", name="简体中文", prefix="", ui_locale="zh-CN"),
        LanguageDefinition(code="en", name="English", prefix="/en", ui_locale="en"),
    ]


class LanguageCatalog(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    default_code: LanguageCode = "zh-CN"
    languages: list[LanguageDefinition] = Field(default_factory=initial_languages, min_length=1)

    @model_validator(mode="after")
    def unique_languages(self) -> Self:
        if len({item.code for item in self.languages}) != len(self.languages):
            raise ValueError("duplicate language code")
        if len({item.prefix for item in self.languages}) != len(self.languages):
            raise ValueError("duplicate language prefix")
        default = next((item for item in self.languages if item.code == self.default_code), None)
        if default is None or not default.enabled:
            raise ValueError("default language must be enabled")
        if any(not item.prefix and item.code != self.default_code for item in self.languages):
            raise ValueError("only the default language may use an empty prefix")
        return self


async def language_catalog(session: AsyncSession) -> LanguageCatalog:
    from app.modules.content.models import SiteSettings

    record = await session.get(SiteSettings, 1)
    return (
        LanguageCatalog.model_validate(record.data.get("language_catalog", {}))
        if record
        else LanguageCatalog()
    )


async def require_language(session: AsyncSession, code: str | None) -> LanguageDefinition:
    from app.modules.auth.service import AuthFailure

    catalog = await language_catalog(session)
    chosen = code or catalog.default_code
    item = next((item for item in catalog.languages if item.code == chosen and item.enabled), None)
    if item is None:
        raise AuthFailure(422, 42291, "此语言未启用，请先在站点设置中添加或启用")
    return item


def preferred_language(catalog: LanguageCatalog, requested: str | None) -> str:
    enabled = {item.code for item in catalog.languages if item.enabled}
    candidates: list[tuple[float, int, str]] = []
    for order, entry in enumerate((requested or "").split(",")):
        parts = entry.strip().split(";")
        try:
            quality = next(
                (float(part.strip()[2:]) for part in parts[1:] if part.strip().startswith("q=")),
                1.0,
            )
        except ValueError:
            continue
        if 0 < quality <= 1:
            candidates.append((-quality, order, canonical_language(parts[0])))
    for _, _, code in sorted(candidates):
        if code in enabled:
            return code
        base = code.split("-", 1)[0]
        match = next(
            (
                item.code
                for item in catalog.languages
                if item.enabled and item.code.split("-", 1)[0] == base
            ),
            None,
        )
        if match:
            return match
    return catalog.default_code


def language_route_pattern(catalog: LanguageCatalog) -> str | None:
    import re

    prefixes = [
        re.escape(item.prefix) for item in catalog.languages if item.enabled and item.prefix
    ]
    if not prefixes:
        return None
    return (
        "^(?:"
        + "|".join(prefixes)
        + r")(?:/?$|/(?:article|ss|search|archives|categories|tags)/?$|/(?:category|tag)/)"
    )


def reserved_language_path(path: str, catalog: LanguageCatalog) -> bool:
    import re

    pattern = language_route_pattern(catalog)
    return bool(pattern and re.search(pattern, path))
