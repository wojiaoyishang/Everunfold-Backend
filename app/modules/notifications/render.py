"""Restricted notification interpolation: no template code is executed."""

import html
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")
OPTIONAL_EMAIL_ELEMENT = re.compile(
    r'<(img|a)\b[^<>]*?\bdata-mail-optional="([a-zA-Z_][a-zA-Z0-9_.]*)"'
    r"[^<>]*>",
)
OPTIONAL_EMAIL_LINK_END = re.compile(r"[^<]*</a>")


def value_at(data: Mapping[str, Any], path: str) -> str:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return ""
        value = value[part]
    return "" if value is None else str(value)


def render_template(template: str, data: Mapping[str, Any]) -> str:
    return TOKEN.sub(lambda match: value_at(data, match.group(1)), template)


def render_html_template(template: str, data: Mapping[str, Any]) -> str:
    """Interpolate HTML templates without allowing event data to introduce markup."""
    # Built-in optional links contain only a text label. Images have no closing tag.
    parts: list[str] = []
    offset = 0
    for match in OPTIONAL_EMAIL_ELEMENT.finditer(template):
        end = match.end()
        if match.group(1) == "a":
            closing = OPTIONAL_EMAIL_LINK_END.match(template, end)
            if closing is None:
                continue
            end = closing.end()
        parts.append(template[offset : match.start()])
        if value_at(data, match.group(2)):
            parts.append(template[match.start() : end])
        offset = end
    parts.append(template[offset:])
    return TOKEN.sub(
        lambda match: html.escape(value_at(data, match.group(1)), quote=True),
        "".join(parts),
    )


def header_value(value: str, limit: int = 300) -> str:
    """Prevent event data from injecting additional mail headers."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:limit]


ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
VOID_TAGS = {"br", "hr", "img"}
GLOBAL_ATTRIBUTES = {"class", "style", "title"}
TAG_ATTRIBUTES = {
    "a": {"href", "rel", "target"},
    "img": {"alt", "height", "src", "width"},
    "table": {"border", "cellpadding", "cellspacing", "role", "width"},
    "td": {"align", "colspan", "rowspan", "valign", "width"},
    "th": {"align", "colspan", "rowspan", "valign", "width"},
}
DROP_WITH_CONTENT = {"form", "iframe", "math", "object", "script", "style", "svg"}


def safe_url(value: str, *, image: bool = False) -> str:
    value = value.strip()
    lowered = value.lower()
    allowed: tuple[str, ...] = ("http://", "https://", "/")
    if image:
        allowed += ("cid:",)
    else:
        allowed += ("mailto:",)
    if "\x00" in value or not lowered.startswith(allowed):
        return ""
    return value


def safe_style(value: str) -> str:
    lowered = value.lower().replace(" ", "")
    return "" if "javascript:" in lowered or "expression(" in lowered else value


class EmailHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        allowed_names = GLOBAL_ATTRIBUTES | TAG_ATTRIBUTES.get(tag, set())
        values: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed_names or value is None:
                continue
            if name in {"href", "src"}:
                value = safe_url(value, image=name == "src")
            elif name == "style":
                value = safe_style(value)
            elif name == "target" and value != "_blank":
                value = ""
            if value or name == "alt":
                values.append(f' {name}="{html.escape(value, quote=True)}"')
        rendered = f"<{tag}{''.join(values)}>"
        self.parts.append(rendered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if not self.drop_depth and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&#{name};")


def sanitize_email_html(value: str) -> str:
    parser = EmailHTMLSanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)
