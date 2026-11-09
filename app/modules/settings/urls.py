"""Shared URL values for public settings and deployment-owned presentation metadata."""

from urllib.parse import urlsplit


def safe_url(value: str) -> str:
    if not value:
        return value
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("invalid URL")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("requires a site path or HTTP(S) URL")
    return value
