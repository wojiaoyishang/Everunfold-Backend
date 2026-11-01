"""Original preservation, active content rejection and recursive menu boundaries."""

import base64
import hashlib
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from app.modules.auth.service import AuthFailure
from app.modules.media.storage import LocalObjectStorage
from app.modules.media.validation import validate_icon
from app.modules.settings.schemas import SitePreferences


def test_static_svg_and_png_are_preserved() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        b'<path fill="currentColor" d="M1 1h20v20z"/></svg>'
    )
    assert validate_icon(base64.b64encode(svg).decode()) == (svg, "image/svg+xml", "svg")
    stream = io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(stream, format="PNG")
    data = stream.getvalue()
    assert validate_icon(base64.b64encode(data).decode()) == (data, "image/png", "png")


@pytest.mark.parametrize(
    "svg",
    [
        "<svg><script>alert(1)</script></svg>",
        '<svg onload="alert(1)"/>',
        '<svg><image href="https://example.test/"/></svg>',
        '<!DOCTYPE svg [<!ENTITY x "bad">]><svg>&x;</svg>',
        '<svg><path style="fill: red"/></svg>',
        '<svg><path fill="url(https://example.test/a.svg)"/></svg>',
        "<svg><foreignObject/></svg>",
        '<?xml-stylesheet href="x"?><svg/>',
        '<svg><use href="#a"/></svg>',
    ],
)
def test_active_or_external_svg_rejected(svg: str) -> None:
    with pytest.raises(AuthFailure):
        validate_icon(base64.b64encode(svg.encode()).decode())


def test_immutable_storage_is_retryable_and_cannot_escape(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "uploads")
    key = "2026/11/id/original.png"
    digest = hashlib.sha256(b"original").hexdigest()
    storage.publish(key, b"original", digest)
    storage.publish(key, b"original", digest)
    with pytest.raises(OSError):
        storage.publish(key, b"other", hashlib.sha256(b"other").hexdigest())
    assert storage.path(key).read_bytes() == b"original"
    assert not list(tmp_path.rglob("*.part"))
    for bad in ("../x", "E:/x", "/x", "..\\x"):
        with pytest.raises(ValueError):
            storage.path(bad)


def test_recursive_menus_validate_every_descendant() -> None:
    leaf: dict[str, Any] = {"id": "leaf", "label": "Leaf", "href": "/leaf"}
    tree = leaf
    for index in range(40):
        tree = {"id": f"n{index}", "label": "Node", "href": "/", "children": [tree]}
    payload = {"name": "Site", "author": "Author", "description": "", "navigation": [tree]}
    value = SitePreferences.model_validate(payload)
    assert value.navigation[0].children[0].children
    leaf["id"] = "n0"
    with pytest.raises(ValidationError):
        SitePreferences.model_validate(payload)
    leaf["id"] = "leaf"
    leaf["href"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        SitePreferences.model_validate(payload)
