"""Bounded, immutable copying of referenced passive public media."""

from __future__ import annotations

import hashlib
import html
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

SAFE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".avif",
    ".ico",
    ".mp3",
    ".mp4",
    ".pdf",
}


class MediaResolver:
    def __init__(
        self, backup: Path, origin: str, destination: Path | None, public_prefix: str, dry_run: bool
    ) -> None:
        self.backup = backup.resolve()
        self.host = urlsplit(origin).hostname
        self.destination = destination.resolve() if destination else None
        self.public_prefix = public_prefix.rstrip("/")
        self.dry_run = dry_run
        self.references: dict[str, dict[str, str]] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.issues: Counter[str] = Counter()
        self.media_issues: set[tuple[str, str]] = set()

    def issue(self, reason: str, path: str) -> None:
        self.issues[reason] += 1
        self.media_issues.add((reason, path))

    def resolve(self, url: str) -> str:
        parsed = urlsplit(html.unescape(url))
        if parsed.scheme not in {"", "http", "https"}:
            return url
        if parsed.hostname and parsed.hostname not in {self.host, "www." + str(self.host)}:
            return url
        path = unquote(parsed.path)
        if not path.startswith("/wp-content/uploads/"):
            return url
        source = (self.backup / path.lstrip("/")).resolve()
        if not source.is_relative_to(self.backup / "wp-content" / "uploads"):
            self.issue("unsafe_media_path", path)
            return ""
        if source.suffix.lower() not in SAFE_EXTENSIONS:
            self.issue("unsupported_active_or_unknown_media", path)
            return ""
        if not source.is_file():
            webp = source.with_name(source.name + ".webp")
            if webp.is_file():
                source = webp
                self.issue("missing_variant_replaced_by_existing_webp", path)
            else:
                self.issue("missing_local_media", path)
                return url
        if source.stat().st_size > 32 * 1024 * 1024:
            self.issue("oversized_media", path)
            return url
        media_id = str(uuid5(NAMESPACE_URL, "wordpress-media:" + path))
        if media_id not in self.files:
            data = source.read_bytes()
            suffix = source.suffix.lower()
            valid_magic = (
                suffix in {".jpg", ".jpeg"}
                and data.startswith(b"\xff\xd8\xff")
                or suffix == ".png"
                and data.startswith(b"\x89PNG\r\n\x1a\n")
                or suffix == ".gif"
                and data.startswith(b"GIF8")
                or suffix == ".webp"
                and data.startswith(b"RIFF")
                and data[8:12] == b"WEBP"
                or suffix == ".ico"
                and data.startswith(b"\0\0\x01\0")
                or suffix in {".avif", ".mp4"}
                and data[4:8] == b"ftyp"
                or suffix == ".pdf"
                and data.startswith(b"%PDF-")
                or suffix == ".mp3"
                and (
                    data.startswith(b"ID3")
                    or len(data) > 2
                    and data[0] == 255
                    and data[1] & 224 == 224
                )
            )
            if not valid_magic:
                self.issue("media_type_mismatch", path)
                return ""
            if sum(item["bytes"] for item in self.files.values()) + len(data) > 256 * 1024 * 1024:
                raise ValueError("Referenced public media exceeds the 256 MiB extraction budget")
            digest = hashlib.sha256(data).hexdigest()
            name = digest[:24] + source.suffix.lower()
            public_url = self.public_prefix + "/" + name if self.destination else url
            if self.destination and not self.dry_run:
                self.destination.mkdir(parents=True, exist_ok=True)
                target = self.destination / name
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise ValueError("Existing media hash mismatch; refusing to overwrite")
                else:
                    shutil.copyfile(source, target)
            self.files[media_id] = {
                "id": media_id,
                "source_path": path,
                "sha256": digest,
                "bytes": len(data),
                "url": public_url,
            }
        self.references[media_id] = {"original": str(self.files[media_id]["url"])}
        return "site://media/" + media_id
