"""Bounded bitmap edits. Every output is a new immutable original, never an overwrite."""

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from app.modules.auth.service import AuthFailure
from app.modules.media.media_validation import MAX_IMAGE_BYTES, validate_media
from app.modules.media.schemas import ImageRecipe

BITMAP_MIMES = {"image/png", "image/jpeg", "image/webp"}


def inspect_bitmap(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if image.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            return width, height
    except (OSError, ValueError):
        return None, None


def transform_bitmap(path: Path, recipe: ImageRecipe) -> tuple[bytes, str, str]:
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("source too large")
        data = path.read_bytes()
        mime, extension = validate_media(data)
        if mime not in BITMAP_MIMES:
            raise ValueError("not a bitmap")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
            if recipe.rotation:
                image = image.transpose(
                    {
                        90: Image.Transpose.ROTATE_270,
                        180: Image.Transpose.ROTATE_180,
                        270: Image.Transpose.ROTATE_90,
                    }[recipe.rotation]
                )
            if recipe.flip_horizontal:
                image = ImageOps.mirror(image)
            if recipe.flip_vertical:
                image = ImageOps.flip(image)
            if crop := recipe.crop:
                if crop.x + crop.width > image.width or crop.y + crop.height > image.height:
                    raise ValueError("crop outside image")
                image = image.crop((crop.x, crop.y, crop.x + crop.width, crop.y + crop.height))
            if recipe.output_width:
                if recipe.output_width > image.width:
                    raise ValueError("upscaling is not supported")
                height = max(
                    1, (image.height * recipe.output_width + image.width // 2) // image.width
                )
                image = image.resize((recipe.output_width, height), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            # Keep the source format; no GPS/EXIF is copied into the edited file.
            if mime == "image/jpeg":
                image.convert("RGB").save(output, "JPEG", quality=92)
            elif mime == "image/webp":
                image.save(output, "WEBP", quality=90)
            else:
                image.save(output, "PNG")
            result = output.getvalue()
            validate_media(result)
            return result, mime, extension
    except (
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise AuthFailure(422, 42235, "无法处理图片，请检查裁剪范围和输出尺寸") from exc
