"""Safe text projection and node-scoped legacy media rewriting."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from html.parser import HTMLParser


class TextProjection(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = TextProjection()
    parser.feed(value)
    return re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()


class NonCodeTextProjection(TextProjection):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"pre", "code"}:
            self.hidden += 1
        else:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"pre", "code"} and self.hidden:
            self.hidden -= 1
        else:
            super().handle_endtag(tag)


def text_outside_code(value: str) -> str:
    parser = NonCodeTextProjection()
    parser.feed(value)
    return "".join(parser.parts)


class MediaHTMLRewriter(HTMLParser):
    """Rewrite URL attributes only; code and ordinary text stay literal."""

    def __init__(
        self, resolve: Callable[[str], str], link_resolve: Callable[[str], str] | None = None
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.resolve = resolve
        self.link_resolve = link_resolve
        self.parts: list[str] = []
        self.code_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"pre", "code"}:
            self.code_depth += 1
        if self.code_depth or tag not in {"img", "a", "source", "video", "audio"}:
            self.parts.append(self.get_starttag_text() or f"<{tag}>")
            return
        rewritten = []
        for key, value in attrs:
            if value is not None and key in {"src", "href", "poster"}:
                previous = value
                value = self.resolve(value)
                if tag == "a" and key == "href" and value == previous and self.link_resolve:
                    value = self.link_resolve(value)
            elif value is not None and key == "srcset":
                entries = []
                for entry in value.split(","):
                    fields = entry.strip().split()
                    if fields:
                        entries.append(" ".join([self.resolve(fields[0]), *fields[1:]]))
                value = ", ".join(entries)
            rewritten.append(key if value is None else f'{key}="{html.escape(value, quote=True)}"')
        self.parts.append(f"<{tag}{' ' if rewritten else ''}{' '.join(rewritten)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.parts[-1] = self.parts[-1].removesuffix(">") + " />"

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if tag in {"pre", "code"} and self.code_depth:
            self.code_depth -= 1

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


def rewrite_html(
    value: str, resolve: Callable[[str], str], link_resolve: Callable[[str], str] | None = None
) -> str:
    parser = MediaHTMLRewriter(resolve, link_resolve)
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def rewrite_markdown(
    value: str, resolve: Callable[[str], str], link_resolve: Callable[[str], str] | None = None
) -> str:
    """Conservatively rewrite balanced inline links outside Markdown code.

    Fenced and indented code is kept byte-for-byte. Unsupported reference-link
    definitions are left alone rather than guessing their semantic destination.
    """
    output: list[str] = []
    fence: tuple[str, int] | None = None
    for line in value.splitlines(keepends=True):
        if fence is not None:
            marker, size = fence
            if re.match(r"^ {0,3}" + re.escape(marker) + "{" + str(size) + r",}\s*$", line):
                fence = None
            output.append(line)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            output.append(line)
            continue
        if line.startswith(("    ", "\t")):
            output.append(line)
            continue
        index = 0
        brackets: list[int] = []
        parts: list[str] = []
        segments: list[str] = []
        while index < len(line):
            if line[index] == "\\" and index + 1 < len(line):
                parts.append(line[index : index + 2])
                index += 2
                continue
            if line[index] == "`":
                end = index
                while end < len(line) and line[end] == "`":
                    end += 1
                size = end - index
                closing = re.search(r"(?<!`)`{" + str(size) + r"}(?!`)", line[end:])
                if closing:
                    close_end = end + closing.end()
                    segments.append(rewrite_html("".join(parts), resolve, link_resolve))
                    parts = []
                    segments.append(line[index:close_end])
                    index = close_end
                    continue
                parts.append(line[index:end])
                index = end
                continue
            if line[index] == "[":
                brackets.append(index)
            if line[index] == "]" and brackets:
                opening_index = brackets.pop()
                if not line.startswith("](", index):
                    parts.append(line[index])
                    index += 1
                    continue
                start = index + 2
                end = start
                escaped = False
                depth = 0
                quote: str | None = None
                while end < len(line):
                    char = line[end]
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif quote:
                        if char == quote:
                            quote = None
                    elif char in {'"', "'"} and end > start and line[end - 1].isspace():
                        quote = char
                    elif char == "(":
                        depth += 1
                    elif char == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    end += 1
                if end < len(line):
                    destination = line[start:end]
                    match = re.fullmatch(r"(<[^>]+>|[^\s<>]+)(\s+['\"].*['\"])?", destination)
                    if match:
                        url, title = match.groups()
                        title = title or ""
                        wrapped = url.startswith("<") and url.endswith(">")
                        original_url = url[1:-1] if wrapped else url
                        replacement = resolve(original_url)
                        is_image = opening_index > 0 and line[opening_index - 1] == "!"
                        if not is_image and replacement == original_url and link_resolve:
                            replacement = link_resolve(original_url)
                        parts.append(
                            "](" + (f"<{replacement}>" if wrapped else replacement) + title + ")"
                        )
                        index = end + 1
                        continue
            parts.append(line[index])
            index += 1
        segments.append(rewrite_html("".join(parts), resolve, link_resolve))
        output.append("".join(segments))
    return "".join(output)
