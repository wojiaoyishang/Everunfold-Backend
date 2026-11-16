"""CAPTCHA policy, public projection and one-time challenge regression checks."""

import asyncio
import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.modules.captcha.image import generate_image
from app.modules.captcha.providers import provider_catalog
from app.modules.captcha.schemas import (
    CaptchaConfig,
    ChallengeRequest,
    SaveProvider,
    StoredProvider,
)
from app.modules.captcha.service import required, settings_state
from app.modules.content.models import Content, Taxonomy
from app.modules.content.service import PublicContentService


def test_nested_contract_graphs_include_the_same_captcha_and_private_read_fields() -> None:
    from app.modules.content.editor_schema import EditorSettings
    from app.modules.pages.schemas import PublicManagedPage

    root = Path(__file__).resolve().parents[1] / "contracts"
    expected_page = PublicManagedPage.model_json_schema()
    expected_page.pop("$defs", None)
    expected_editor = EditorSettings.model_json_schema()
    expected_editor.pop("$defs", None)
    for filename in (
        "content-admin-v1.schema.json",
        "pages-v1.schema.json",
    ):
        artifact = json.loads((root / filename).read_text())
        assert artifact["$defs"]["PublicManagedPage"] == expected_page
    # Only the private unlock response carries protected-page comments. The
    # public page schema must remain free of these authorized-only fields.
    from app.modules.captcha.page_access import UnlockedManagedPage

    expected_unlocked = UnlockedManagedPage.model_json_schema()
    expected_unlocked.pop("$defs", None)
    captcha = json.loads((root / "captcha-v1.schema.json").read_text())
    assert captcha["$defs"]["UnlockedManagedPage"] == expected_unlocked
    for name, schema in expected_page["properties"].items():
        assert expected_unlocked["properties"][name] == schema
    assert "unlocked_comments" not in expected_page["properties"]
    for filename in ("content-admin-v1.schema.json", "collaboration-v1.schema.json"):
        artifact = json.loads((root / filename).read_text())
        assert artifact["$defs"]["EditorSettings"] == expected_editor


def test_default_rules_explicit_overrides_and_unlimited_provider_ids() -> None:
    config = CaptchaConfig()
    assert not required(config, "read", "article", {})
    assert required(config, "read", "article", {"captcha_read": "required"})
    config.rules.read.article = True
    assert required(config, "read", "article", {})
    assert not required(config, "read", "article", {"captcha_read": "off"})
    assert not required(config, "read", "shuoshuo", {})
    assert not required(config, "comment", "article", {})
    assert set(provider_catalog()) >= {"google.recaptcha-v2", "cloudflare.turnstile"}


def test_raster_image_contains_no_answer_in_metadata_and_uses_random_answers() -> None:
    answers = set()
    for _ in range(4):
        answer, data = generate_image()
        answers.add(answer)
        assert len(answer) == 5
        image = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
        assert image.format == "PNG" and image.size == (220, 76) and image.info == {}
    assert len(answers) == 4


def test_secret_is_write_only_and_hostname_has_no_wildcard_or_url() -> None:
    config = CaptchaConfig(providers={"google.recaptcha-v2": StoredProvider(secret="never-echo")})
    assert "never-echo" not in settings_state(config).model_dump_json()
    assert "never-echo" not in repr(config)
    for value in ["*.example.com", "http://example.com", "example.com:3000", "example.com/path"]:
        with pytest.raises(ValidationError):
            SaveProvider(hostnames=[value])
    for action, target in [("login", uuid4()), ("read", None), ("comment", None)]:
        with pytest.raises(ValidationError):
            ChallengeRequest.model_validate(
                {"action": action, "content_id": target, "client_id": uuid4()}
            )


@pytest.mark.parametrize("access", ["captcha", "password"])
def test_protected_article_shows_cover_but_requires_unlock_for_body_and_terms(access: str) -> None:
    now = datetime.now(UTC)
    category = Taxonomy(
        id=uuid4(), slug="private-category", name="Private category", kind="category"
    )
    tag = Taxonomy(id=uuid4(), slug="private-tag", name="Private tag", kind="tag")
    item = Content(
        id=uuid4(),
        kind="article",
        title="Visible title",
        path="/captcha-test",
        author="a",
        excerpt="secret excerpt",
        content=[{"id": "one", "type": "markdown", "content": "secret body"}],
        cover_url="/media/cover.webp",
        media_urls={"x": {"original": "secret media"}},
        language="en",
        published_at=now,
        updated_at=now,
        word_count=11,
        reading_minutes=1,
        publication_status="published",
        visibility="password" if access == "password" else "public",
        access_password_hash="private hash" if access == "password" else None,
        captcha_read_required=access == "captcha",
        editor_settings={},
        pinned=False,
        taxonomies=[category, tag],
    )
    projection = PublicContentService._detail(item, {}, {})
    assert projection.access == access
    assert projection.captcha_required == (access == "captcha")
    assert projection.password_required == (access == "password")
    assert projection.cover_url == item.cover_url
    assert not projection.categories and not projection.tags
    assert projection.content == [] and not projection.media_urls and projection.excerpt == ""
    assert "secret" not in projection.model_dump_json()
    unlocked = PublicContentService._detail(item, {}, {}, authorized=True)
    assert unlocked.content and unlocked.access == "public"
    assert [(term.slug, term.count) for term in unlocked.categories] == [(category.slug, 0)]
    assert [(term.slug, term.count) for term in unlocked.tags] == [(tag.slug, 0)]


def test_google_and_turnstile_verify_success_requires_exact_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx2

    from app.modules.captcha.providers import verify_site_token
    from app.modules.captcha.schemas import StoredProvider

    async def run() -> None:
        reply: dict[str, object] = {"success": True}
        transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json=reply))
        original = httpx2.AsyncClient

        def factory(**kwargs: Any) -> httpx2.AsyncClient:
            return original(transport=transport)

        monkeypatch.setattr(httpx2, "AsyncClient", factory)
        for hostname, expected in [
            ("example.com", True),
            ("other.example.com", False),
            (None, False),
        ]:
            reply["hostname"] = hostname
            assert (
                await verify_site_token(
                    "https://test.invalid/siteverify",
                    "token",
                    StoredProvider(secret="private", hostnames=["example.com"]),
                )
                is expected
            )

    asyncio.run(run())
