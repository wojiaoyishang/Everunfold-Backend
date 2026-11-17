"""Private rule editing and the public projection of page customizations."""

import re
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.core.schemas import PublicModel

PLUGIN_ID = "core.page-injections"
STORE_KEY = "page_injection_config"
Surface = Literal["public", "admin", "account"]


class PageInjection(PublicModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    enabled: bool
    scope: Literal["all", "public", "admin", "account", "top-menu"]
    page_type: Literal[
        "", "home", "article", "moment", "page", "archive", "search", "glossary", "admin", "account"
    ] = ""
    path: str = Field(default="", max_length=512)
    html: str = Field(default="", max_length=16000)
    css: str = Field(default="", max_length=16000)
    javascript: str = Field(default="", max_length=16000)

    @field_validator("name")
    @classmethod
    def title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rule name is required")
        return value.strip()

    @field_validator("path")
    @classmethod
    def path_pattern(cls, value: str) -> str:
        if value and (
            not value.startswith("/")
            or value.startswith("//")
            or re.search(r"[?#\\\s\x00-\x1f\x7f]", value)
            or "***" in value
            or value.count("*") > 16
        ):
            raise ValueError("use a local pathname with optional * or ** wildcards")
        return value

    def can_target(self, surface: Surface) -> bool:
        if self.scope not in {"all", surface} and not (
            surface == "public" and self.scope == "top-menu"
        ):
            return False
        if self.page_type == "":
            return True
        if self.page_type in {"admin", "account"}:
            return self.page_type == surface
        return surface == "public"

    @model_validator(mode="after")
    def valid_target(self) -> Self:
        if not any(self.can_target(place) for place in ("public", "admin", "account")):
            raise ValueError("page type is incompatible with the selected scope")
        return self


class PageInjectionRules(PublicModel):
    rules: list[PageInjection] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def bounded_rules(self) -> Self:
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule identifiers")
        if (
            sum(len((rule.html + rule.css + rule.javascript).encode()) for rule in self.rules)
            > 64000
        ):
            raise ValueError("rule sources exceed 64 KB in total")
        return self


class PageInjectionState(PageInjectionRules):
    revision: int = Field(default=0, ge=0)


class SavePageInjections(PageInjectionRules):
    expected_revision: int = Field(ge=0)
