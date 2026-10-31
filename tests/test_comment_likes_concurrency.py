"""Two real processes must not double-count the same anonymous browser."""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.content.comment_likes import CommentLikeService
from app.modules.content.models import Comment, Content


@pytest.mark.anyio
@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="Dedicated PostgreSQL test database required",
)
async def test_like_ack_survives_independent_processes() -> None:
    settings = load_settings("config.test.local.toml")
    assert make_url(settings.database.url.get_secret_value()).database == "everunfold_test"
    database = Database(settings.database.url.get_secret_value())
    content_id, comment_id, visitor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        async with database.session_factory() as session:
            now = datetime.now(UTC)
            session.add(
                Content(
                    id=content_id,
                    source="test",
                    kind="article",
                    title="Like concurrency test",
                    path=f"/test-{content_id}",
                    published_at=now,
                    updated_at=now,
                    author="Test",
                    content=[],
                )
            )
            await session.flush()
            session.add(
                Comment(
                    id=comment_id,
                    content_id=content_id,
                    author="Test",
                    content="Test comment",
                    created_at=now,
                )
            )
            await session.commit()
        script = """
import asyncio, sys
from uuid import UUID
from app.core.config import load_settings
from app.core.database import Database
from app.modules.content.comment_likes import CommentLikeService
async def run():
    db = Database(load_settings('config.test.local.toml').database.url.get_secret_value())
    try:
        async with db.session_factory() as session:
            result = await CommentLikeService(session).set(
                UUID(sys.argv[1]), UUID(sys.argv[2]), True)
            assert result.count == 1 and result.liked
    finally:
        await db.close()
asyncio.run(run())
"""
        processes = [
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                str(comment_id),
                str(visitor),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            for _ in range(3)
        ]
        for process in processes:
            _, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()[-1000:]
        async with database.session_factory() as session:
            state = (await CommentLikeService(session).read([comment_id], visitor))[0]
            assert state.count == 1 and state.liked
    finally:
        async with database.session_factory() as session:
            await session.execute(delete(Content).where(Content.id == content_id))
            await session.commit()
        await database.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
