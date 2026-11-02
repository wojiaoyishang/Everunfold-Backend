"""Plain previews for source blocks; the full renderer remains a frontend responsibility."""

import re
from html import unescape
from typing import Any


def content_preview(blocks: list[dict[str, Any]], length: int = 180) -> str:
    text = "\n".join(str(block.get("content", "")) for block in blocks)
    text = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", "", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*_`>]", "", text)
    return " ".join(unescape(text).split())[:length]
