"""Real image decoding and container checks for the general media library."""

import io
from pathlib import Path

import pytest
from PIL import Image

from app.modules.auth.service import AuthFailure
from app.modules.media.media_validation import validate_media

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("extension,mime", [("mp4", "video/mp4"), ("webm", "video/webm")])
def test_real_video_containers_and_truncation(extension: str, mime: str) -> None:
    data = (FIXTURES / f"library-preview.{extension}").read_bytes()
    assert validate_media(data) == (mime, extension)
    with pytest.raises(AuthFailure):
        validate_media(data[: len(data) // 2])


@pytest.mark.parametrize(
    "format,extension,mime",
    [("JPEG", "jpg", "image/jpeg"), ("PNG", "png", "image/png"), ("WEBP", "webp", "image/webp")],
)
def test_image_decoded_by_content_not_filename(format: str, extension: str, mime: str) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (17, 23), "blue").save(stream, format=format)
    assert validate_media(stream.getvalue()) == (mime, extension)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not-an-image",
        b"<html>payload</html>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"\x00\x00\x00\x10ftypisom0000",
        b"\x1a\x45\xdf\xa3\x00",
    ],
)
def test_malformed_or_active_content_is_rejected(data: bytes) -> None:
    with pytest.raises(AuthFailure) as error:
        validate_media(data)
    assert error.value.status == 422


def test_animated_images_and_oversized_dimensions_are_rejected() -> None:
    frames = [Image.new("RGB", (16, 16), color) for color in ("blue", "red")]
    stream = io.BytesIO()
    frames[0].save(
        stream, format="WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0
    )
    with pytest.raises(AuthFailure):
        validate_media(stream.getvalue())
    stream = io.BytesIO()
    Image.new("RGB", (8193, 1), "blue").save(stream, format="PNG")
    with pytest.raises(AuthFailure):
        validate_media(stream.getvalue())
