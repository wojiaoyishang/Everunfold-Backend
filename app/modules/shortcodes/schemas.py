"""Native shortcode sources and the public, compiled presentation contract."""

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.core.schemas import PublicModel
from app.modules.content.schemas import MediaURL
from app.modules.settings.publishing_preferences import DEFAULT_SHORTCODE_DEPTH, MAX_SHORTCODE_DEPTH


class ShortcodeModel(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ShortcodeParameter(ShortcodeModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=60)
    type: Literal["text", "url", "select", "number", "boolean"] = "text"
    default: str = Field(default="", max_length=1000)
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_options(self) -> Self:
        if self.name == "content":
            raise ValueError("content is reserved for the body slot")
        if self.type == "select" and (
            not self.options
            or len(set(self.options)) != len(self.options)
            or any(not item or len(item) > 100 for item in self.options)
            or self.default not in self.options
        ):
            raise ValueError("select requires unique options and a matching default")
        if self.type != "select" and self.options:
            raise ValueError("options are only valid for select parameters")
        if self.type == "boolean" and self.default not in {"true", "false"}:
            raise ValueError("boolean default must be true or false")
        if self.type == "number":
            import math

            try:
                if not math.isfinite(float(self.default)):
                    raise ValueError("number must be finite")
            except ValueError as error:
                raise ValueError("number requires a finite default") from error
        return self


class ShortcodeSource(ShortcodeModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}(?::[a-z][a-z0-9-]{0,47})?$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    kind: Literal["leaf", "container"] = "container"
    parameters: list[ShortcodeParameter] = Field(default_factory=list, max_length=16)
    html: str = Field(min_length=1, max_length=8000)
    css: str = Field(default="", max_length=8000)
    javascript: str = Field(default="", max_length=8000, exclude_if=lambda value: not value)
    example_body: str = Field(default="这里是正文内容。", max_length=2000)

    @model_validator(mode="after")
    def unique_parameters(self) -> Self:
        if len({item.name for item in self.parameters}) != len(self.parameters):
            raise ValueError("duplicate parameter name")
        return self


class PublicShortcode(ShortcodeModel):
    id: str
    name: str
    title: str
    description: str
    kind: Literal["leaf", "container"]
    parameters: list[ShortcodeParameter]
    html: str
    css: str
    javascript: str = Field(default="", max_length=8000, exclude_if=lambda value: not value)
    scope: str = Field(pattern=r"^ef-sc-[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    media_urls: dict[str, str] = Field(default_factory=dict)


class ShortcodeItem(ShortcodeModel):
    id: str
    origin: Literal["backend", "custom"]
    editable: bool
    revision: int
    applied_revision: int | None
    archived: bool
    source: ShortcodeSource
    compiled: PublicShortcode
    versions: list[int] = Field(default_factory=list)


class ShortcodeCatalog(ShortcodeModel):
    items: list[ShortcodeItem]
    applied: list[PublicShortcode] = Field(default_factory=list)
    settings_revision: int
    shortcode_max_depth: int = Field(
        default=DEFAULT_SHORTCODE_DEPTH,
        ge=1,
        le=MAX_SHORTCODE_DEPTH,
        exclude_if=lambda value: value == DEFAULT_SHORTCODE_DEPTH,
    )


class SaveShortcode(ShortcodeModel):
    id: str | None = None
    expected_revision: int = Field(ge=0)
    value: ShortcodeSource


class ShortcodeAction(ShortcodeModel):
    id: str
    expected_revision: int = Field(ge=1)


class RestoreShortcode(ShortcodeAction):
    version: int = Field(ge=1)


class PreviewShortcode(ShortcodeModel):
    id: str | None = None
    value: ShortcodeSource


class ShortcodeCompilation(ShortcodeModel):
    compiled: PublicShortcode | None
    error: str


class RegionPasswordInput(ShortcodeModel):
    password: str = Field(min_length=1, max_length=200, repr=False)


class RegionPasswordResult(ShortcodeModel):
    credential: str


class RegionUnlock(ShortcodeModel):
    id: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
    password: str = Field(default="", max_length=200, repr=False)


class RegionBody(ShortcodeModel):
    type: Literal["markdown", "html"]
    source: str
    media_urls: dict[str, MediaURL]
