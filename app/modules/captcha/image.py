"""Raster challenges: the answer is never encoded in SVG, metadata or public state."""

import base64
import io
import secrets

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def generate_image() -> tuple[str, str]:
    answer = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    image = Image.new("RGB", (220, 76), "#f0f3fa")
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        draw.line(
            (
                secrets.randbelow(220),
                secrets.randbelow(76),
                secrets.randbelow(220),
                secrets.randbelow(76),
            ),
            fill=(
                170 + secrets.randbelow(50),
                170 + secrets.randbelow(50),
                170 + secrets.randbelow(50),
            ),
            width=1,
        )
    font = ImageFont.load_default(size=38)
    for index, char in enumerate(answer):
        layer = Image.new("RGBA", (50, 64))
        ImageDraw.Draw(layer).text((4, 5), char, font=font, fill=(35, 51, 83))
        layer = layer.rotate(secrets.randbelow(25) - 12, resample=Image.Resampling.BICUBIC)
        image.paste(layer, (8 + index * 41, 3 + secrets.randbelow(7)), layer)
    draw = ImageDraw.Draw(image)
    for _ in range(130):
        draw.point((secrets.randbelow(220), secrets.randbelow(76)), fill="#8190ad")
    image = image.filter(ImageFilter.SMOOTH)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return answer, "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
