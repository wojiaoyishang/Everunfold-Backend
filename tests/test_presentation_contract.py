"""Draft presentation boundary checks; no database or runtime endpoint involved."""

import pytest
from pydantic import ValidationError

from app.modules.content.presentation_schema import ContentPresentation


def record(**changes: object) -> dict[str, object]:
    return {
        "id": "example",
        "type": "shuoshuo",
        "title": "",
        "path": "/shuoshuo/example",
        "excerpt": "",
        "language": "zh-CN",
        "published_at": "2026-10-31T08:00:00Z",
        "updated_at": "2026-10-31T08:00:00Z",
        "author": "示例",
        "cover_url": None,
        "reading_minutes": 0,
        "word_count": 0,
        "categories": [],
        "tags": [],
        "comment_count": 0,
        "pinned": False,
        "content": [],
        "media_urls": {},
        "comments_open": True,
        "reaction_count": 0,
        "access": "public",
        "opens_at": None,
        **changes,
    }


def test_shuoshuo_may_omit_title_but_article_must_have_one() -> None:
    assert ContentPresentation.model_validate(record()).type == "shuoshuo"
    with pytest.raises(ValidationError, match="Only shuoshuo"):
        ContentPresentation.model_validate(record(type="article"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", [{"id": "b1", "type": "markdown", "content": "restricted"}]),
        ("excerpt", "restricted"),
        ("media_urls", {"m1": {"original": "/restricted.jpg"}}),
    ],
)
def test_locked_projection_does_not_leak_body(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match="Locked projections"):
        ContentPresentation.model_validate(record(access="password", **{field: value}))


def test_scheduled_projection_requires_opening_time() -> None:
    with pytest.raises(ValidationError, match="opening time"):
        ContentPresentation.model_validate(record(access="scheduled"))
    value = ContentPresentation.model_validate(
        record(access="scheduled", opens_at="2026-11-01T00:00:00Z")
    )
    assert not value.content
