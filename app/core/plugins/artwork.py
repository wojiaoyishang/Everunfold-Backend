"""Load an explicitly declared raster preview from a deployed plugin."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_MEDIA_TYPES = {
    "gif": "image/gif",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class PluginArtwork:
    filename: str
    media_type: str
    body: bytes


def read_artwork(source: Path, preview: str) -> PluginArtwork:
    match = re.fullmatch(r"asset:(assets/[a-zA-Z0-9_-]+\.(gif|jpeg|jpg|png|webp))", preview)
    if match is None:
        raise ValueError(f"Invalid plugin preview asset in {source.name}")
    relative, extension = match.groups()
    file = (source / relative).resolve()
    if not file.is_relative_to(source.resolve()) or not file.is_file():
        raise ValueError(f"Plugin preview asset is missing or outside {source.name}")
    with file.open("rb") as stream:
        body = stream.read(_MAX_BYTES + 1)
    if not 0 < len(body) <= _MAX_BYTES:
        raise ValueError(f"Plugin preview asset exceeds package limits in {source.name}")
    digest = hashlib.sha256(body).hexdigest()
    return PluginArtwork(f"preview.{digest}.{extension}", _MEDIA_TYPES[extension], body)
