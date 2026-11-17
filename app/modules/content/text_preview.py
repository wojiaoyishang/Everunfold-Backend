"""Plain previews for source blocks; the full renderer remains a frontend responsibility."""

import re
from html import unescape
from typing import Any

from app.modules.shortcodes.prose import without_glossary_popups

DEFAULT_EXCERPT_LENGTH = 180


def content_preview(blocks: list[dict[str, Any]], length: int = DEFAULT_EXCERPT_LENGTH) -> str:
    text = "\n".join(without_glossary_popups(str(block.get("content", ""))) for block in blocks)
    text = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", "", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*_`>]", "", text)
    return " ".join(unescape(text).split())[:length]
