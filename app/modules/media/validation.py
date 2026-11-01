"""Bounded, non-rewriting validation: reject unsafe originals rather than sanitize in place."""

import base64
import binascii
import io
import re
import warnings
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

from app.modules.auth.service import AuthFailure

SVG_TAGS = set(
    [
        "svg",
        "g",
        "defs",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "title",
        "desc",
    ]
)
SVG_ATTRS = set(
    [
        "id",
        "viewBox",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "d",
        "points",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-opacity",
        "opacity",
        "transform",
        "clip-path",
        "clip-rule",
        "mask",
        "offset",
        "stop-color",
        "stop-opacity",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "fx",
        "fy",
        "fr",
        "preserveAspectRatio",
        "version",
        "xmlns",
    ]
)


def validate_svg(data: bytes) -> None:
    text = data.decode("utf-8-sig")
    if re.search(r"<!|<\?(?!xml\s)", text, re.I):
        raise ValueError("SVG declarations and processing instructions are not supported")
    root = ET.fromstring(text)
    nodes = list(root.iter())
    if len(nodes) > 2048 or root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
        raise ValueError("invalid SVG root or complexity")
    for element in nodes:
        tag = element.tag.removeprefix("{http://www.w3.org/2000/svg}")
        if tag not in SVG_TAGS:
            raise ValueError("unsupported SVG element")
        for key, value in element.attrib.items():
            if key not in SVG_ATTRS or len(value) > 65536:
                raise ValueError("unsupported SVG attribute")
            # Only local fragment paint/clip references; no CSS, events or embedded content.
            if re.search(r"url\s*\(", value, re.I) and not re.fullmatch(
                r"url\(#[A-Za-z_][A-Za-z0-9_.-]*\)", value
            ):
                raise ValueError("external SVG reference")
            if any(char in value for char in ("\\", "@", ";")):
                raise ValueError("unsupported SVG attribute value")


def validate_icon(encoded: str) -> tuple[bytes, str, str]:
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > 1048576:
            raise ValueError("file size")
        if data.lstrip().startswith((b"<", b"\xef\xbb\xbf")):
            validate_svg(data)
            return data, "image/svg+xml", "svg"
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("unsupported image format")
                if not (1 <= image.width <= 2048 and 1 <= image.height <= 2048):
                    raise ValueError("image dimensions")
                extension = image.format.lower()
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            return data, f"image/{extension}", extension
    except (
        ValueError,
        binascii.Error,
        UnicodeError,
        ET.ParseError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise AuthFailure(
            422,
            42230,
            "请选择 1 MB 内的静态 SVG、PNG 或 WebP；位图不超过 2048px，"
            "SVG 不得含脚本、样式或外部引用",
        ) from exc
