"""The root initializer creates an empty database and preserves existing content/accounts."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.core.config import DatabaseConfig, load_settings
from app.core.database import SCHEMA_REVISION, Database
from app.modules.auth.models import User
from app.modules.auth.security import hash_password, verify_password
from app.modules.content.models import Comment, CommentLike, Content


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database test required"
)
def test_root_initializer_creates_migrates_and_never_resets_existing_data(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    settings = load_settings(root / "config.test.local.toml")
    original_url = make_url(settings.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    database_name = "everunfold_init_test_" + uuid4().hex[:12]
    protected_name = "protected_existing"
    url = original_url.set(database=database_name).render_as_string(hide_password=False)
    target = settings.model_copy(update={"database": DatabaseConfig.model_validate({"url": url})})
    environment = {
        **os.environ,
        "DATABASE_URL": url,
        "EVERUNFOLD_ADMIN_USERNAME": "admin",
        "PYTHONIOENCODING": "utf-8",
    }
    environment.pop("EVERUNFOLD_ADMIN_PASSWORD", None)
    command = [
        sys.executable,
        str(root / "init.database.py"),
        "--config",
        str(root / "config.test.local.toml"),
    ]

    def invoke(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command, *extra],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    async def inspect(*, seed_content: bool) -> tuple[str, int, int, int, str, str, bool]:
        database = Database(target.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                assert revision == SCHEMA_REVISION
                admin = await session.scalar(select(User).where(User.username == "admin"))
                assert admin is not None and admin.role == "admin"
                assert verify_password(admin.password_hash, "123456")
                protected = await session.scalar(
                    select(User).where(User.username == protected_name)
                )
                if seed_content:
                    now, content_id, comment_id = datetime.now(UTC), uuid4(), uuid4()
                    session.add(
                        Content(
                            id=content_id,
                            source="test",
                            kind="article",
                            title="Keep this content",
                            path="/preserved-test",
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
                            content="Keep this comment",
                            created_at=now,
                        )
                    )
                    await session.flush()
                    session.add(
                        CommentLike(comment_id=comment_id, visitor_hash="a" * 64, created_at=now)
                    )
                    session.add(
                        User(
                            id=uuid4(),
                            username=protected_name,
                            display_name="Protected Existing",
                            password_hash=hash_password("existing-password"),
                            role="user",
                            active=False,
                            created_at=now,
                        )
                    )
                    await session.commit()
                    protected = await session.scalar(
                        select(User).where(User.username == protected_name)
                    )
                assert protected is not None
                assert protected.role == "user" and protected.active is False
                assert verify_password(protected.password_hash, "existing-password")
                counts = [
                    int(await session.scalar(select(func.count()).select_from(model)) or 0)
                    for model in (Content, Comment, CommentLike)
                ]
                return (
                    admin.password_hash,
                    counts[0],
                    counts[1],
                    counts[2],
                    protected.password_hash,
                    protected.role,
                    protected.active,
                )
        finally:
            await database.close()

    async def remove_test_database() -> None:
        assert database_name.startswith("everunfold_init_test_")
        maintenance = original_url.set(database="postgres").render_as_string(hide_password=False)
        database = Database(maintenance)
        try:
            async with database.engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text('DROP DATABASE IF EXISTS "' + database_name + '"'))
        finally:
            await database.close()

    try:
        absent = invoke("--no-create-database")
        assert absent.returncode == 1
        first = invoke()
        assert first.returncode == 0, first.stderr
        assert "123456" not in first.stdout + first.stderr
        before = asyncio.run(inspect(seed_content=True))
        environment["EVERUNFOLD_ADMIN_PASSWORD"] = "must-not-replace-existing-password"
        repeated = invoke()
        assert repeated.returncode == 0, repeated.stderr
        protected = invoke("--admin-username", protected_name)
        assert protected.returncode == 0, protected.stderr
        assert "同名账号已存在" in protected.stdout
        assert asyncio.run(inspect(seed_content=False)) == before
        assert before[1:4] == (1, 1, 1)
    finally:
        asyncio.run(remove_test_database())
