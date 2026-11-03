"""Versioned ProseMirror/Y.XmlFragment projection; raw HTML is never silently discarded."""

import html
import json
import re
from html.parser import HTMLParser
from typing import Any

from pycrdt import Map, XmlElement, XmlFragment, XmlText

TAGS = {
    "p": "paragraph",
    "blockquote": "blockquote",
    "ul": "bulletList",
    "ol": "orderedList",
    "li": "listItem",
    "pre": "codeBlock",
    "hr": "horizontalRule",
    "br": "hardBreak",
    "img": "image",
    "table": "table",
    "tr": "tableRow",
    "td": "tableCell",
    "th": "tableHeader",
}
MARKS = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "s": "strike",
    "del": "strike",
    "u": "underline",
    "code": "code",
    "a": "link",
    "span": "textStyle",
}
VOIDS = {"hr", "br", "img"}
NODE_ATTRS = {
    "paragraph": {"textAlign"},
    "heading": {"textAlign", "level"},
    "orderedList": {"start", "type"},
    "codeBlock": {"language"},
    "image": {"src", "alt", "title", "width", "height", "caption", "alignment"},
    "tableCell": {"colspan", "rowspan", "colwidth", "align"},
    "tableHeader": {"colspan", "rowspan", "colwidth", "align"},
    "rawHtml": {"html"},
}
MARK_ATTRS = {
    "bold": set(),
    "italic": set(),
    "strike": set(),
    "underline": set(),
    "code": set(),
    "link": {"href", "target", "rel", "class", "title"},
    "textStyle": {"fontId", "fontSize", "color"},
}


class SourceTree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": "root", "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOIDS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOIDS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if len(self.stack) < 2 or self.stack[-1]["tag"] != tag:
            raise ValueError("unbalanced HTML")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1]["children"].append(data)

    def handle_comment(self, data: str) -> None:
        raise ValueError("preserve comments in source")


def source_document(source: str) -> dict[str, Any]:
    """Conservative import: unsupported structure remains one lossless, source-editable atom."""
    try:
        parser = SourceTree()
        parser.feed(source)
        parser.close()
        if len(parser.stack) != 1:
            raise ValueError("unclosed HTML")
        nodes = convert_children(
            [part for part in parser.root["children"] if not isinstance(part, str) or part.strip()]
        )
        if any(n["type"] == "text" for n in nodes):
            if any(n["type"] not in {"text", "hardBreak"} for n in nodes):
                raise ValueError("mixed root nodes")
            nodes = [{"type": "paragraph", "content": nodes}]
        tree = {"type": "doc", "content": nodes or [{"type": "paragraph"}]}
        render_node(tree)
        return tree
    except (ValueError, TypeError, KeyError):
        return {"type": "doc", "content": [{"type": "rawHtml", "attrs": {"html": source}}]}


def convert_children(
    children: list[Any], marks: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for child in children:
        if isinstance(child, str):
            if child:
                output.append(
                    {"type": "text", "text": child, **({"marks": marks} if marks else {})}
                )
            continue
        tag, attributes = child["tag"], child["attrs"]
        style = attributes.get("style", "") or ""
        css = dict(part.strip().split(":", 1) for part in style.split(";") if ":" in part)
        css = {k.strip().lower(): v.strip() for k, v in css.items()}
        if tag in {"thead", "tbody", "tfoot"} and not attributes:
            output.extend(convert_children(child["children"], marks))
            continue
        if tag in MARKS:
            attrs: dict[str, Any] = {}
            if tag == "a":
                if set(attributes) - MARK_ATTRS["link"]:
                    raise ValueError("unsupported link")
                attrs = attributes
            elif tag == "span":
                if set(attributes) - {"data-font", "style"} or set(css) - {
                    "font-size",
                    "color",
                    "font-family",
                }:
                    raise ValueError("unsupported text style")
                if attributes.get("data-font"):
                    attrs["fontId"] = attributes["data-font"].replace("site://media/", "media:")
                if "font-family" in css and not attrs.get("fontId"):
                    raise ValueError("preserve arbitrary font family")
                for key, field in (("font-size", "fontSize"), ("color", "color")):
                    if key in css:
                        attrs[field] = css[key]
            elif attributes:
                raise ValueError("unsupported mark attributes")
            output.extend(
                convert_children(
                    child["children"], [*(marks or []), {"type": MARKS[tag], "attrs": attrs}]
                )
            )
            continue
        heading = re.fullmatch(r"h([1-6])", tag)
        kind = "heading" if heading else TAGS.get(tag)
        if kind is None:
            raise ValueError("unknown element")
        attrs = {"level": int(heading[1])} if heading else {}
        if style and kind in {"paragraph", "heading"} and set(css) <= {"text-align"}:
            if css.get("text-align") not in {"left", "center", "right", "justify"}:
                raise ValueError("unknown alignment")
            attrs["textAlign"] = css["text-align"]
        elif style:
            raise ValueError("preserve custom styles")
        for key, value in attributes.items():
            if key == "style":
                continue
            if key not in NODE_ATTRS.get(kind, set()):
                raise ValueError("unknown attributes")
            attrs[key] = (
                int(value)
                if key in {"start", "rowspan", "colspan", "width", "height"} and value
                else value
            )
        source_children = child["children"]
        if kind in {"bulletList", "orderedList", "table", "tableRow", "listItem"}:
            source_children = [
                part for part in source_children if not isinstance(part, str) or part.strip()
            ]
        content = convert_children(source_children, marks)
        if kind == "codeBlock":
            content = (
                [{"type": "text", "text": "".join(n.get("text", "") for n in content)}]
                if content
                else []
            )
        node: dict[str, Any] = {"type": kind, "attrs": attrs}
        if content:
            node["content"] = content
        output.append(node)
    return output


def attr_string(attrs: dict[str, Any]) -> str:
    def escaped(value: Any) -> str:
        text = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        return html.escape(text, quote=True)

    return "".join(
        f' {key}="{escaped(value)}"'
        for key, value in attrs.items()
        if value is not None and value != ""
    )


def render_node(node: dict[str, Any], depth: int = 0) -> str:
    if depth > 64 or not isinstance(node, dict):
        raise ValueError("rich text nesting")
    kind, attrs = node.get("type"), node.get("attrs") or {}
    if not isinstance(attrs, dict) or set(attrs) - NODE_ATTRS.get(str(kind), set()):
        raise ValueError("unknown rich node attributes")
    if kind == "text":
        value = html.escape(node.get("text", ""), quote=False)
        for mark in reversed(node.get("marks", [])):
            name, props = mark["type"], mark.get("attrs") or {}
            if name not in MARK_ATTRS or set(props) - MARK_ATTRS[name]:
                raise ValueError("unknown rich mark")
            tag = {
                "bold": "strong",
                "italic": "em",
                "strike": "s",
                "underline": "u",
                "code": "code",
                "link": "a",
                "textStyle": "span",
            }[name]
            attributes: dict[str, Any] = {}
            if name == "link":
                attributes = {key: props.get(key) for key in ("href", "title", "target", "rel")}
            if name == "textStyle":
                styles = []
                font = props.get("fontId")
                if font:
                    if not re.fullmatch(r"media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", font):
                        raise ValueError("font identity")
                    attributes["data-font"] = font.replace("media:", "site://media/")
                    styles.append("font-family:ef-font-" + font[6:])
                size = props.get("fontSize")
                if size:
                    if not re.fullmatch(r"(?:[8-9]|[1-6][0-9]|7[0-2])px", size):
                        raise ValueError("font size")
                    styles.append("font-size:" + size)
                color = props.get("color")
                if color:
                    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                        raise ValueError("text color")
                    styles.append("color:" + color)
                attributes["style"] = ";".join(styles)
            value = f"<{tag}{attr_string(attributes)}>{value}</{tag}>"
        return value
    content = "".join(render_node(part, depth + 1) for part in node.get("content", []))
    if kind == "doc":
        return content
    if kind == "rawHtml":
        raw = attrs.get("html", "")
        if not isinstance(raw, str) or len(raw) > 100_000:
            raise ValueError("raw HTML size")
        return raw
    reverse = {value: key for key, value in TAGS.items()}
    if kind == "heading":
        level = attrs.get("level", 2)
        if level not in range(1, 7):
            raise ValueError("heading level")
        tag = f"h{int(level)}"
    elif kind in reverse:
        tag = reverse[kind]
    else:
        raise ValueError("unknown rich node")
    attributes = {}
    alignment = attrs.get("textAlign")
    if alignment:
        if alignment not in {"left", "center", "right", "justify"}:
            raise ValueError("alignment")
        attributes["style"] = "text-align:" + alignment
    for key in ("start", "src", "alt", "title", "width", "height", "colspan", "rowspan"):
        if key in attrs:
            attributes[key] = attrs[key]
    if kind == "codeBlock":
        language = attrs.get("language")
        suffix = (
            attr_string({"class": "language-" + language})
            if isinstance(language, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", language)
            else ""
        )
        return f"<pre><code{suffix}>{content}</code></pre>"
    return f"<{tag}{attr_string(attributes)}>" + ("" if tag in VOIDS else content + f"</{tag}>")


def xml_document(fragment: XmlFragment) -> dict[str, Any]:
    count = 0

    def children(container: XmlFragment | XmlElement, depth: int = 0) -> list[dict[str, Any]]:
        nonlocal count
        if depth > 64:
            raise ValueError("rich text nesting")
        result: list[dict[str, Any]] = []
        for child in container.children:
            count += 1
            if count > 10_000:
                raise ValueError("too many rich nodes")
            if isinstance(child, XmlText):
                for text, attrs in child.diff():
                    if not isinstance(text, str):
                        raise ValueError("embedded rich object")
                    if text:
                        marks = [
                            {"type": key, "attrs": value or {}}
                            for key, value in sorted((attrs or {}).items())
                        ]
                        result.append({"type": "text", "text": text, "marks": marks})
            elif isinstance(child, XmlElement):
                result.append(
                    {
                        "type": child.tag,
                        "attrs": dict(child.attributes.__iter__()),
                        "content": children(child, depth + 1),
                    }
                )
            else:
                raise ValueError("unknown rich fragment")
        return result

    return {"type": "doc", "content": children(fragment)}


def write_fragment(fragment: XmlFragment, document: dict[str, Any]) -> None:
    render_node(document)
    del fragment.children[:]

    def append(parent: XmlFragment | XmlElement, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node["type"] == "text":
                value = XmlText(node.get("text", ""))
                parent.children.append(value)
                attributes = {m["type"]: m.get("attrs") or {} for m in node.get("marks", [])}
                if attributes:
                    value.format(0, len(value), attributes)
            else:
                element = XmlElement(node["type"])
                parent.children.append(element)
                for key, val in (node.get("attrs") or {}).items():
                    element.attributes[key] = val
                append(element, node.get("content", []))

    append(fragment, document.get("content", []))


def initialize_block(block: Map[Any]) -> None:
    fragment = XmlFragment()
    block["rich"] = fragment
    write_fragment(fragment, source_document(str(block["content"])))
    block["rich_base"] = render_node(xml_document(fragment))


def projected_html(block: Map[Any]) -> str:
    if block["type"] != "html" or "rich" not in block:
        return str(block["content"])
    if not isinstance(block["rich"], XmlFragment) or not isinstance(block.get("rich_base"), str):
        raise ValueError("rich schema")
    rendered = render_node(xml_document(block["rich"]))
    return str(block["content"]) if rendered == block["rich_base"] else rendered


def rich_fingerprint(block: Map[Any]) -> str:
    return (
        json.dumps(xml_document(block["rich"]), ensure_ascii=False, sort_keys=True)
        if "rich" in block
        else str(block["content"])
    )
