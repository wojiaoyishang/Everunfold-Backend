"""Old imports leave public APIs and a real receiving node without removing stored rows."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import load_settings
from app.core.database import Database
from app.modules.content.models import Comment, Content, SearchDocument
from app.modules.content.publication import ContentPublication
from tools.withdraw_wordpress_preview import run


def check_imported_preview(
    client: Any,
    sql: Any,
    state: Any,
    snapshot: Any,
    wait_for: Any,
    config: Path,
    native_id: str,
) -> None:
    identities = [uuid4(), uuid4()]

    async def seed() -> None:
        settings = load_settings(config)
        database = Database(settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                from app.modules.media.locking import lock_references

                await lock_references(session)
                for identity, kind in zip(identities, ["article", "page"], strict=True):
                    session.add(
                        Content(
                            id=identity,
                            source="wordpress",
                            kind=kind,
                            title="Old imported preview",
                            path=f"/legacy-{identity}.html",
                            published_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                            author="Test author",
                            content=[
                                {"id": str(uuid4()), "type": "markdown", "content": "Old text"}
                            ],
                        )
                    )
                await session.flush()
                session.add(
                    Comment(
                        id=uuid4(),
                        content_id=identities[0],
                        author="Legacy visitor",
                        content="Legacy comment",
                        created_at=datetime.now(UTC),
                    )
                )
                for identity in identities:
                    session.add(SearchDocument(content_id=identity, title="Old import", body="Old"))
                await ContentPublication(session, settings).stage()
                await session.commit()
        finally:
            await database.close()

    asyncio.run(seed())
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert asyncio.run(run(config, apply=False, username="admin")) == {"eligible": 2}
    revision = state()["revision"]
    assert {str(item) for item in identities}.issubset({c["id"] for c in snapshot()["contents"]})
    # An audit failure rolls back both the withdrawal and its delivery intent.
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT import_audit_failure "
            "CHECK (changes->>'action' IS DISTINCT FROM 'withdraw-imported-preview')"
        )
    )
    with pytest.raises(IntegrityError):
        asyncio.run(run(config, apply=True, username="admin"))
    assert state()["revision"] == revision
    assert asyncio.run(run(config, apply=False, username="admin")) == {"eligible": 2}
    asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT import_audit_failure"))
    assert asyncio.run(run(config, apply=True, username="admin")) == {
        "withdrawn": 2,
        "revision": revision + 1,
    }
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    applied = snapshot()
    for identity in identities:
        assert all(c["id"] != str(identity) for c in applied["contents"])
        assert str(identity) not in applied["comments"]
        assert (
            client.get(
                "/v1/contents/resolve", params={"path": f"/legacy-{identity}.html"}
            ).status_code
            == 404
        )
        stored = asyncio.run(sql("SELECT publication_status FROM contents WHERE id=$1", identity))
        assert stored[0]["publication_status"] == "withdrawn"
    assert any(c["id"] == native_id for c in applied["contents"])
    assert not client.get("/v1/contents", params={"q": "Old import"}).json()["data"]["items"]
    assert asyncio.run(run(config, apply=True, username="admin")) == {
        "withdrawn": 0,
        "revision": None,
    }
    assert state()["revision"] == revision + 1
