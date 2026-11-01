"""Pixel-level checks of orientation, editing bounds and immutable originals."""

import io
from pathlib import Path

import pytest
from PIL import Image

from app.modules.auth.service import AuthFailure
from app.modules.media.image_processing import inspect_bitmap, transform_bitmap
from app.modules.media.schemas import CropRect, ImageRecipe


def test_rotation_crop_resize_preserves_original_and_alpha(tmp_path: Path) -> None:
    source = Image.new("RGBA", (12, 8), (80, 100, 200, 0))
    source.paste((255, 0, 0, 255), (0, 0, 6, 4))
    path = tmp_path / "original.png"
    source.save(path)
    original = path.read_bytes()
    result, mime, extension = transform_bitmap(
        path, ImageRecipe(rotation=90, crop=CropRect(x=4, y=0, width=4, height=6), output_width=2)
    )
    with Image.open(io.BytesIO(result)) as edited:
        assert edited.size == (2, 3)
        assert edited.getpixel((0, 0)) == (255, 0, 0, 255)
    assert (mime, extension) == ("image/png", "png")
    assert path.read_bytes() == original
    flipped, _, _ = transform_bitmap(path, ImageRecipe(flip_horizontal=True, flip_vertical=True))
    with Image.open(io.BytesIO(flipped)) as edited:
        pixel = edited.getpixel((0, 0))
        assert isinstance(pixel, tuple) and pixel[3] == 0
        assert edited.getpixel((11, 7)) == (255, 0, 0, 255)


def test_exif_orientation_and_metadata_are_normalized(tmp_path: Path) -> None:
    image = Image.new("RGB", (20, 10), "blue")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private original metadata"
    path = tmp_path / "source.jpg"
    image.save(path, exif=exif)
    assert inspect_bitmap(path) == (10, 20)
    result, _, _ = transform_bitmap(path, ImageRecipe())
    with Image.open(io.BytesIO(result)) as edited:
        assert edited.size == (10, 20)
        assert not edited.getexif()


@pytest.mark.parametrize(
    "recipe",
    [ImageRecipe(output_width=21), ImageRecipe(crop=CropRect(x=19, y=0, width=2, height=2))],
)
def test_invalid_dimensions_rejected(tmp_path: Path, recipe: ImageRecipe) -> None:
    path = tmp_path / "source.png"
    Image.new("RGB", (20, 10)).save(path)
    with pytest.raises(AuthFailure):
        transform_bitmap(path, recipe)
