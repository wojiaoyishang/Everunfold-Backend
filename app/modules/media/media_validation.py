"""Bounded image decoding and video container checks; originals remain unchanged."""

import io
import warnings
from xml.etree import ElementTree as ET

from PIL import Image

from app.modules.auth.service import AuthFailure
from app.modules.media.validation import validate_svg

MAX_MEDIA_BYTES = 128 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def validate_mp4(data: bytes) -> None:
    position = 0
    boxes: set[bytes] = set()
    count = 0
    while position < len(data):
        if len(data) - position < 8 or count > 10000:
            raise ValueError("invalid MP4 box")
        size = int.from_bytes(data[position : position + 4], "big")
        name = data[position + 4 : position + 8]
        header = 8
        if size == 1:
            if len(data) - position < 16:
                raise ValueError("truncated MP4 box")
            size = int.from_bytes(data[position + 8 : position + 16], "big")
            header = 16
        elif size == 0:
            size = len(data) - position
        if size < header or position + size > len(data):
            raise ValueError("truncated MP4 container")
        if position == 0:
            if name != b"ftyp" or size < 16:
                raise ValueError("missing MP4 file type")
            brands = {data[i : i + 4] for i in range(8, min(size, 256), 4)}
            if not brands.intersection(
                {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V "}
            ):
                raise ValueError("unsupported MP4 brand")
        boxes.add(name)
        position += size
        count += 1
    if not {b"ftyp", b"moov", b"mdat"}.issubset(boxes):
        raise ValueError("incomplete MP4")


def vint(data: bytes, offset: int, *, identifier: bool = False) -> tuple[int, int]:
    if offset >= len(data) or data[offset] == 0:
        raise ValueError("invalid EBML integer")
    length = 9 - data[offset].bit_length()
    if offset + length > len(data):
        raise ValueError("truncated EBML integer")
    value = int.from_bytes(data[offset : offset + length], "big")
    if not identifier:
        value &= (1 << (7 * length)) - 1
    return value, offset + length


def validate_webm(data: bytes) -> None:
    header_size, start = vint(data, 4)
    end = start + header_size
    if end > min(len(data), 4096):
        raise ValueError("invalid EBML header")
    doctype = None
    while start < end:
        identifier, start = vint(data, start, identifier=True)
        size, start = vint(data, start)
        if start + size > end:
            raise ValueError("invalid EBML element")
        if identifier == 0x4282:
            doctype = data[start : start + size]
        start += size
    if doctype != b"webm" or data[end : end + 4] != b"\x18\x53\x80\x67":
        raise ValueError("unsupported WebM document")
    segment_size, segment_start = vint(data, end + 4)
    size_width = segment_start - end - 4
    if segment_size != (1 << (7 * size_width)) - 1 and segment_start + segment_size > len(data):
        raise ValueError("truncated WebM segment")
    prefix = data[segment_start : segment_start + 1024 * 1024]
    if b"\x16\x54\xae\x6b" not in prefix or not any(
        codec in prefix for codec in (b"V_VP8", b"V_VP9", b"V_AV1")
    ):
        raise ValueError("missing WebM video track")


def validate_media(data: bytes) -> tuple[str, str]:
    try:
        if not data or len(data) > MAX_MEDIA_BYTES:
            raise ValueError("file size")
        if data[4:8] == b"ftyp":
            validate_mp4(data)
            return "video/mp4", "mp4"
        if data.startswith(b"\x1a\x45\xdf\xa3"):
            validate_webm(data)
            return "video/webm", "webm"
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image file size")
        if data.lstrip().startswith((b"<", b"\xef\xbb\xbf")):
            if len(data) > 1048576:
                raise ValueError("SVG file size")
            validate_svg(data)
            return "image/svg+xml", "svg"
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("unsupported image")
                if max(image.size) > 8192 or image.width * image.height > 40000000:
                    raise ValueError("image dimensions")
                extension = "jpg" if image.format == "JPEG" else image.format.lower()
                mime = Image.MIME[image.format]
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            return mime, extension
    except (
        ValueError,
        UnicodeError,
        ET.ParseError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise AuthFailure(
            422, 42233, "请选择有效的静态 SVG/PNG/JPEG/WebP 图片或 MP4/WebM 视频"
        ) from exc
