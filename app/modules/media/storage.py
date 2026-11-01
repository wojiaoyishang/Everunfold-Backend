"""Replaceable storage boundary. Published originals are create-only and restart-safe."""

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4


class ObjectStorage(Protocol):
    def publish(self, key: str, data: bytes, digest: str) -> None: ...
    def path(self, key: str) -> Path: ...
    def remove(self, key: str, digest: str) -> None: ...
    def matches(self, key: str, digest: str) -> bool: ...


class LocalObjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, key: str) -> Path:
        parts = PurePosixPath(key)
        if parts.is_absolute() or ".." in parts.parts or "\\" in key or ":" in key:
            raise ValueError("invalid object key")
        target = self.root.joinpath(*parts.parts).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("object outside upload directory")
        return target

    def publish(self, key: str, data: bytes, digest: str) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Staging is never served and is on the same filesystem for atomic linking.
        staging = self.root.parent / ".everunfold-media-work" / "local" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{uuid4()}.part"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise OSError("immutable object conflict") from None
            if os.name != "nt":
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def matches(self, key: str, digest: str) -> bool:
        target = self.path(key)
        if not target.is_file():
            return False
        with target.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == digest

    def remove(self, key: str, digest: str) -> None:
        target = self.path(key)
        if not target.exists():
            return
        if not self.matches(key, digest):
            raise OSError("refusing to remove changed original")
        # A single resolved file under the configured uploads root; never recurse.
        target.unlink()
