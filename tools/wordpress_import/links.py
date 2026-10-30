"""Convert only known public same-origin link targets to this deployment's paths."""

from __future__ import annotations

from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


class PublicLinkResolver:
    def __init__(self, origin: str, public_paths: set[str]) -> None:
        self.origin = origin.rstrip("/")
        self.host = urlsplit(origin).hostname
        self.paths = {path.rstrip("/") or "/": path for path in public_paths}
        self.unmapped: set[str] = set()

    def resolve(self, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"", "http", "https"} or value.startswith("#"):
            return value
        if parsed.hostname and parsed.hostname != self.host:
            return value
        if not parsed.hostname and not value.startswith("/"):
            return value
        path = unquote(parsed.path)
        canonical = self.paths.get(path.rstrip("/") or "/")
        if canonical is not None:
            return urlunsplit(("", "", canonical, parsed.query, parsed.fragment))
        self.unmapped.add(path)
        # Missing independent app routes must still reach the original source,
        # instead of silently becoming a local 404 during development.
        return urljoin(self.origin + "/", value)
