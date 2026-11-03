"""Font parsing uses actual sfnt tables, never trusts extensions or CSS names."""

import io

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from app.modules.auth.service import AuthFailure
from app.modules.media.fonts import inspect_font
from app.modules.media.upload_validation import validate_upload


def font_bytes(flavor: str | None = None) -> bytes:
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((500, 800))
    pen.lineTo((900, 0))
    pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (1000, 0), "A": (1000, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Everunfold Test", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.font.flavor = flavor
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


@pytest.mark.parametrize("flavor,kind", [(None, "truetype"), ("woff", "woff"), ("woff2", "woff2")])
def test_font_metadata_and_signature(flavor: str | None, kind: str) -> None:
    data = font_bytes(flavor)
    mime, extension, info = inspect_font(data)
    assert mime.startswith("font/") and extension
    assert info == {
        "family": "Everunfold Test",
        "style": "normal",
        "weight_min": 400,
        "weight_max": 400,
        "format": kind,
    }
    assert validate_upload(data, "renamed.bin", "font") == (mime, extension)


def test_invalid_font_is_not_registered() -> None:
    for payload in (b"<script>font</script>", b"wOF2" + bytes(24)):
        with pytest.raises(AuthFailure):
            validate_upload(payload, "pretend.woff2", "font")
