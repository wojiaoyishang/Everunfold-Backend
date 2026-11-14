"""Bound the data shown to the provider and isolate encrypted plugin storage."""

import base64
import hmac
import os
import re
from html import unescape
from html.parser import HTMLParser

from cryptography.fernet import Fernet

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.shortcodes.regions import PUBLIC_REGION, REGION_TOKEN, split_regions


def cipher(config: Settings, domain: str) -> Fernet:
    master = os.environ.get(config.auth.application_master_key_env, "")
    if not 43 <= len(master) <= 256:
        raise AuthFailure(503, 50389, "服务器尚未配置 AI 写作加密密钥")
    key = hmac.digest(master.encode(), ("everunfold.ai-writing.v1:" + domain).encode(), "sha256")
    return Fernet(base64.urlsafe_b64encode(key))


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "template", "iframe", "object"}:
            self.hidden.append(tag)
        if tag in {"p", "div", "br", "li", "section", "h1", "h2", "td", "tr"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        elif not self.hidden and tag in {"p", "div", "li", "section", "h1", "h2", "td", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def reviewed_text(value: str) -> str:
    # Decode serialization of angle brackets before deciding which ranges are protected.
    decoded = unescape(value)
    decoded = PUBLIC_REGION.sub("", decoded)
    if REGION_TOKEN.search(decoded):
        try:
            decoded, _ = split_regions(decoded, "html")
        except AuthFailure:
            # A malformed protected region cannot safely contribute any visible text.
            return ""
        decoded = PUBLIC_REGION.sub("", decoded)
    if re.search(r"\{\{\s*<\s*/?\s*(?:reply-only|password-area)", decoded, re.I):
        return ""
    decoded = re.sub(r"!?\[([^\]]*)\]\([^\n)]*\)", r"\1", decoded)
    decoded = re.sub(r"\{\{\s*<[^\n]*?\s*>\s*}}", "", decoded)
    parser = TextParser()
    parser.feed(decoded)
    parser.close()
    return " ".join("".join(parser.parts).split())
