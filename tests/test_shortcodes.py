"""HTML/CSS compilation boundaries, independent of a live site."""

import uuid

import pytest

from app.modules.shortcodes.compiler import compile_shortcode
from app.modules.shortcodes.registry import builtin_catalog
from app.modules.shortcodes.schemas import ShortcodeParameter, ShortcodeSource

IDENTITY = str(uuid.uuid4())


def source(**values: object) -> ShortcodeSource:
    return ShortcodeSource.model_validate(
        {
            "name": "custom:reading-tip",
            "title": "阅读提示",
            "kind": "container",
            "parameters": [{"name": "title", "label": "标题", "default": "提示"}],
            "html": '<section class="card"><h3>{{ title }}</h3><div>{{ content }}</div></section>',
            "css": "& {padding:1em}.card > h3, .card p {color:var(--text-color)}",
            **values,
        }
    )


def test_compiler_scopes_html_and_every_selector_with_one_stable_identity() -> None:
    result = compile_shortcode(source(), IDENTITY, 3)
    assert result.scope in result.html
    assert "{{title}}" in result.html and "{{content}}" in result.html
    assert f".{result.scope}{{padding:1em;}}" in result.css
    assert (
        f".{result.scope} .{result.scope}-card > h3,.{result.scope} .{result.scope}-card p"
        in result.css
    )
    assert compile_shortcode(source(), IDENTITY, 4).scope == result.scope
    assert all(not item.editable and item.origin == "backend" for item in builtin_catalog())


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>{{content}}",
        '<img src="javascript:alert(1)">{{content}}',
        '<div onclick="alert(1)">{{content}}</div>',
        '<div style="color:red">{{content}}</div>',
        "<div>{{unknown}}{{content}}</div>",
        '<div title="{{content}}">{{content}}</div>',
        "<div>{{content}}{{content}}</div>",
        "<div>{{content}}</p>",
        '<div class="{{title}}">{{content}}</div>',
        '<a href="{{title}}">link</a>{{content}}',
        "<div>{{content}}</div><!DOCTYPE html>",
    ],
)
def test_unsafe_or_ambiguous_html_is_rejected_without_rewriting_sources(html: str) -> None:
    with pytest.raises(ValueError):
        compile_shortcode(source(html=html), IDENTITY, 1)


@pytest.mark.parametrize(
    "css",
    [
        '@import "https://example.test/a.css";',
        "@font-face {font-family:x;src:url(x)}",
        ".x{background:u\\72l(https://example.test)}",
        ".x{width:expression(alert(1))}",
        "& + body {color:red}",
        "& ~ p {display:none}",
        "&:hover + p {color:red}",
        ".x{position:fixed}",
        ".x{z-index:999999}",
        ".x{bad}",
        ".x{color:red;& .y {color:blue}}",
        '@media all {@import "x";}',
    ],
)
def test_css_cannot_escape_its_component_or_load_external_code(css: str) -> None:
    with pytest.raises(ValueError):
        compile_shortcode(source(css=css), IDENTITY, 1)


def test_media_at_rules_and_html_style_termination_are_bounded() -> None:
    result = compile_shortcode(
        source(
            css=(
                "@media (max-width: 600px) {.card{padding:.5em}} "
                '.card:after{content:"</style><script>bad</script>"}'
            )
        ),
        IDENTITY,
        1,
    )
    assert "</style" not in result.css and "\\3c " in result.css
    assert f".{result.scope} .{result.scope}-card" in result.css


def test_parameter_defaults_and_contexts_are_validated() -> None:
    with pytest.raises(ValueError):
        ShortcodeParameter(name="content", label="body")
    with pytest.raises(ValueError):
        ShortcodeParameter(name="n", label="n", type="number", default="NaN")
    with pytest.raises(ValueError):
        ShortcodeParameter(name="type", label="t", type="select", default="bad", options=["info"])
    result = compile_shortcode(
        source(
            parameters=[
                {"name": "url", "label": "地址", "type": "url", "default": "https://example.test"}
            ],
            html='<a href="{{url}}">打开</a><div>{{content}}</div>',
        ),
        IDENTITY,
        1,
    )
    assert 'href="{{url}}"' in result.html
    with pytest.raises(ValueError):
        compile_shortcode(
            source(
                parameters=[
                    {
                        "name": "url",
                        "label": "地址",
                        "type": "url",
                        "default": "javascript:alert(1)",
                    }
                ],
                html='<a href="{{url}}">打开</a><div>{{content}}</div>',
            ),
            IDENTITY,
            1,
        )
