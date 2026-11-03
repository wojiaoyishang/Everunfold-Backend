"""Only positively validated formats render inline; all other bytes are attachments."""

import base64
import re
from pathlib import PurePosixPath
from typing import Literal

from app.modules.auth.service import AuthFailure
from app.modules.media.media_validation import MAX_MEDIA_BYTES, validate_media
from app.modules.media.validation import validate_icon


def media_kind(mime: str) -> Literal["image", "video", "file"]:
    return (
        "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "file"
    )


def validate_upload(data: bytes, filename: str, purpose: str) -> tuple[str, str]:
    if len(data) > MAX_MEDIA_BYTES:
        raise AuthFailure(413, 41330, "单个文件不超过 128 MB")
    if purpose == "icon":
        if len(data) > 1048576:
            raise AuthFailure(413, 41330, "图标不超过 1 MB")
        _, mime, extension = validate_icon(base64.b64encode(data).decode("ascii"))
        return mime, extension
    if purpose == "font" or data[:4] in {b"wOF2", b"wOFF", b"OTTO", b"\x00\x01\x00\x00"}:
        from app.modules.media.fonts import inspect_font

        mime, extension, _ = inspect_font(data)
        return mime, extension
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower().lstrip(".")
    known = extension in {"png", "jpg", "jpeg", "webp", "svg", "mp4", "webm"}
    signature = (
        data.startswith((b"\x89PNG", b"\xff\xd8", b"\x1a\x45\xdf\xa3"))
        or data[4:8] == b"ftyp"
        or data[8:12] == b"WEBP"
    )
    if known or signature:
        return validate_media(data)
    return "application/octet-stream", extension if re.fullmatch(
        r"[a-z0-9]{1,12}", extension
    ) else "bin"
