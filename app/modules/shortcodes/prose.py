"""Project the visible text of glossary calls for search and excerpts."""

import re

_GLOSSARY_MARKER = re.compile(
    r"(?<!\\){{<\s*(/?)\s*(glossary(?:-popup)?)(?=\s|>)"
    r'(?:[^">]|"(?:[^"\\]|\\.)*")*>}}'
)


def without_glossary_popups(source: str) -> str:
    """Keep trigger text and omit rich explanations, including nested explanations."""
    if "{{<" not in source:
        return source
    pieces: list[str] = []
    cursor = 0
    depth = 0
    for match in _GLOSSARY_MARKER.finditer(source):
        if depth == 0:
            pieces.append(source[cursor : match.start()])
        if match[2] == "glossary-popup":
            depth = max(0, depth - 1) if match[1] else depth + 1
        cursor = match.end()
    if depth == 0:
        pieces.append(source[cursor:])
    return "".join(pieces)
