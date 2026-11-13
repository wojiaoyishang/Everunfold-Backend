"""Compile bounded HTML/CSS sources. No code execution or network access."""

import html
import re
import uuid
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import tinycss2  # type: ignore[import-untyped]

from app.modules.shortcodes.schemas import PublicShortcode, ShortcodeSource

SLOT = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
TAGS = set(
    [
        "p",
        "br",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "code",
        "a",
        "img",
        "figure",
        "figcaption",
        "strong",
        "b",
        "em",
        "i",
        "s",
        "del",
        "u",
        "mark",
        "sup",
        "sub",
        "kbd",
        "abbr",
        "small",
        "time",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "div",
        "span",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "details",
        "summary",
    ]
)
VOIDS = {"br", "hr", "img"}
ATTRS = {
    "class",
    "title",
    "alt",
    "href",
    "src",
    "width",
    "height",
    "open",
    "colspan",
    "rowspan",
    "scope",
    "start",
    "lang",
    "dir",
    "role",
    "aria-label",
    "datetime",
    "data-variant",
}


def safe_url(value: str) -> bool:
    if any(ord(char) < 32 for char in value) or "\\" in value:
        return False
    if value.startswith("site://media/"):
        try:
            return str(uuid.UUID(value[13:])) == value[13:]
        except ValueError:
            return False
    parsed = urlsplit(value)
    return (not parsed.scheme and not parsed.netloc and not value.startswith("//")) or (
        parsed.scheme in {"https", "http", "mailto"}
        and not parsed.username
        and not parsed.password
        and (bool(parsed.hostname) or parsed.scheme == "mailto")
    )


class TemplateParser(HTMLParser):
    def __init__(self, source: ShortcodeSource, scope: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.scope = scope
        self.parameters = {item.name: item for item in source.parameters}
        self.output: list[str] = []
        self.stack: list[str] = []
        self.body_slots = 0

    def slots(self, value: str, attribute: str = "") -> str:
        def replace(match: re.Match[str]) -> str:
            name = match[1]
            if name == "content":
                if attribute or self.source.kind != "container":
                    raise ValueError("content 只能放在容器正文中")
                self.body_slots += 1
            elif name not in self.parameters:
                raise ValueError(f"未定义参数：{name}")
            elif attribute and (
                attribute not in {"href", "title", "alt", "aria-label", "data-variant"}
                or (attribute == "href" and self.parameters[name].type != "url")
                or (attribute == "data-variant" and self.parameters[name].type != "select")
            ):
                raise ValueError("属性插槽只支持文本属性与 URL 类型的 href")
            return "{{" + name + "}}"

        normalized = SLOT.sub(replace, value)
        if "{{" in SLOT.sub("", value) or "}}" in SLOT.sub("", value):
            raise ValueError("模板插槽格式无效")
        if attribute == "href" and SLOT.search(normalized) and not SLOT.fullmatch(normalized):
            raise ValueError("href 必须是完整 URL 插槽")
        return normalized

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in TAGS:
            raise ValueError(f"模板不支持标签：{tag}")
        if len(self.stack) >= 30 or len(attrs) > 20:
            raise ValueError("模板结构过深或属性过多")
        values: list[str] = []
        names: set[str] = set()
        for name, raw in attrs:
            if name not in ATTRS or name in names:
                raise ValueError(f"模板不支持属性：{name}")
            names.add(name)
            value = self.slots(raw or "", name)
            if name == "class":
                classes = value.split()
                if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", item) for item in classes):
                    raise ValueError("class 仅支持静态类名")
                value = " ".join(self.scope + "-" + item for item in classes)
            if name in {"href", "src"} and not SLOT.fullmatch(value) and not safe_url(value):
                raise ValueError("模板链接或媒体地址无效")
            values.append(f' {name}="{html.escape(value, quote=True)}"')
        self.output.append("<" + tag + "".join(values) + ">")
        if tag not in VOIDS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOIDS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("模板标签未正确配对")
        self.output.append("</" + tag + ">")

    def handle_data(self, data: str) -> None:
        self.output.append(html.escape(self.slots(data), quote=False))

    def handle_comment(self, data: str) -> None:
        # Comments are source-only and never become insertion points.
        pass

    def handle_decl(self, decl: str) -> None:
        raise ValueError("模板不支持文档声明")

    def unknown_decl(self, data: str) -> None:
        raise ValueError("模板声明无效")


def inspect_tokens(
    tokens: list[Any], scope: str, *, selector: bool = False, depth: int = 0
) -> None:
    if depth > 30:
        raise ValueError("CSS 表达式嵌套过深")
    for index, token in enumerate(tokens):
        if token.type in {"error", "url", "at-keyword", "{} block"}:
            raise ValueError("CSS 包含无效或不支持的内容")
        if token.type == "function":
            if token.lower_name in {"url", "expression", "image-set", "-webkit-image-set"}:
                raise ValueError("CSS 不支持外部资源和脚本表达式")
            inspect_tokens(token.arguments, scope, selector=selector, depth=depth + 1)
        if hasattr(token, "content"):
            inspect_tokens(token.content, scope, selector=selector, depth=depth + 1)
        if (
            selector
            and token.type == "ident"
            and index
            and tokens[index - 1].type == "literal"
            and tokens[index - 1].value == "."
        ):
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", token.value):
                raise ValueError("CSS 类名无效")
            token.value = scope + "-" + token.value
        if selector and token.type == "hash":
            raise ValueError("CSS 使用 class 选择器，不支持 ID")


def compile_css(source: str, scope: str, depth: int = 0) -> str:
    if depth > 4:
        raise ValueError("CSS 嵌套过深")
    result = []
    for rule in tinycss2.parse_stylesheet(source, skip_comments=True, skip_whitespace=True):
        if (
            rule.type == "at-rule"
            and rule.lower_at_keyword in {"media", "supports"}
            and rule.content is not None
        ):
            inspect_tokens(rule.prelude, scope)
            result.append(
                "@"
                + rule.lower_at_keyword
                + " "
                + tinycss2.serialize(rule.prelude)
                + "{"
                + compile_css(tinycss2.serialize(rule.content), scope, depth + 1)
                + "}"
            )
            continue
        if rule.type != "qualified-rule":
            raise ValueError("CSS 仅支持样式规则、@media 和 @supports")
        inspect_tokens(rule.prelude, scope, selector=True)
        groups: list[list[Any]] = [[]]
        for token in rule.prelude:
            if token.type == "literal" and token.value == ",":
                groups.append([])
            else:
                groups[-1].append(token)
        selectors = []
        for group in groups:
            part = tinycss2.serialize(group).strip()
            if not part:
                raise ValueError("CSS 选择器不能为空")
            if "&" in part:
                # Root selectors are intentionally small: '&', '& .child', '& > .child'.
                if part != "&" and (
                    not part.startswith("& ") or part[2:].lstrip().startswith(("+", "~", "|"))
                ):
                    raise ValueError("根选择器使用 & 或 & 后跟后代/子元素")
                if part.count("&") != 1:
                    raise ValueError("CSS 根选择器不能嵌套")
                selectors.append("." + scope + part[1:])
            else:
                selectors.append("." + scope + " " + part)
        declarations = []
        for declaration in tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        ):
            if declaration.type != "declaration":
                raise ValueError("CSS 声明格式无效，不支持嵌套选择器")
            inspect_tokens(declaration.value, scope)
            if declaration.lower_name in {"behavior", "-moz-binding", "z-index"}:
                raise ValueError("CSS 属性超出短代码范围")
            value = tinycss2.serialize(declaration.value).strip()
            if declaration.lower_name == "position" and value not in {"static", "relative"}:
                raise ValueError("CSS 定位仅支持 static/relative")
            declarations.append(
                declaration.lower_name
                + ":"
                + value
                + ("!important" if declaration.important else "")
                + ";"
            )
        result.append(",".join(selectors) + "{" + "".join(declarations) + "}")
    # Never let a CSS string terminate its containing HTML style element.
    return "".join(result).replace("<", "\\3c ")


def compile_shortcode(source: ShortcodeSource, identity: str, revision: int) -> PublicShortcode:
    scope = "ef-sc-" + uuid.UUID(identity).hex
    parser = TemplateParser(source, scope)
    parser.feed(source.html)
    parser.close()
    if parser.stack:
        raise ValueError("模板标签未闭合")
    if parser.body_slots != (1 if source.kind == "container" else 0):
        raise ValueError("容器模板需要且只能包含一个 {{ content }} 插槽")
    for parameter in source.parameters:
        if parameter.type == "url" and parameter.default and not safe_url(parameter.default):
            raise ValueError("URL 参数默认值无效")
    return PublicShortcode(
        id=identity,
        name=source.name,
        title=source.title,
        description=source.description,
        kind=source.kind,
        parameters=source.parameters,
        html="".join(parser.output),
        css=compile_css(source.css, scope),
        javascript=source.javascript,
        scope=scope,
        revision=revision,
    )
