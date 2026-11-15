"""Opt-in: a disposable PostgreSQL database exercises the real deployment commands."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
import yaml
from sqlalchemy.engine import make_url

from app.core.database import SCHEMA_REVISION
from app.modules.auth.models import User
from app.modules.auth.security import verify_password
from tests.test_deployment_config import write_root


@pytest.mark.skipif(
    not os.environ.get("EVERUNFOLD_DEPLOY_TEST_DATABASE_URL")
    or not os.environ.get("EVERUNFOLD_DEPLOY_TEST_REDIS_URL"),
    reason="Requires an isolated PostgreSQL account with CREATEDB and test Redis",
)
def test_real_empty_database_preparation_and_existing_database_protection(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "deploy"))
    from database_setup import INITIALIZE_LOCK

    maintenance = make_url(os.environ["EVERUNFOLD_DEPLOY_TEST_DATABASE_URL"])
    name = "everunfold_deploy_test_" + uuid4().hex[:12]
    dsn = maintenance.set(drivername="postgresql", database=name).render_as_string(
        hide_password=False
    )
    config = write_root(tmp_path)
    document = yaml.safe_load(config.read_text())
    initial_password = "isolated-initial-admin-password"
    document["environment"].update(
        DATABASE_URL=maintenance.set(
            drivername="postgresql+asyncpg", database=name
        ).render_as_string(hide_password=False),
        REDIS_URL=os.environ["EVERUNFOLD_DEPLOY_TEST_REDIS_URL"],
        CELERY_BROKER_URL=urlsplit(os.environ["EVERUNFOLD_DEPLOY_TEST_REDIS_URL"])
        ._replace(path="/1")
        .geturl(),
        EVERUNFOLD_ADMIN_PASSWORD=initial_password,
        EVERUNFOLD_AUTO_INIT_EMPTY_DB="0",
    )
    document["application"]["redis"]["namespace_scope"] = name
    environment = {
        key: value for key, value in os.environ.items() if key not in document["environment"]
    }
    environment.update(CONFIG_FILE=str(config), EVERUNFOLD_DATA_DIR=str(tmp_path / "data"))

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        config.write_text(yaml.safe_dump(document))
        config.chmod(0o600)
        result = subprocess.run(
            [sys.executable, str(root / "deploy/runtime_check.py"), *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert initial_password not in result.stdout + result.stderr
        return result

    async def run() -> None:
        owner = await asyncpg.connect(
            maintenance.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        created = False
        try:
            await owner.execute(f'CREATE DATABASE "{name}"')
            created = True
            db = await asyncpg.connect(dsn)
            try:
                assert invoke().returncode == 1
                disabled = invoke("--prepare")
                assert disabled.returncode == 1 and "已关闭" in disabled.stdout
                assert await db.fetchval("SELECT to_regclass('alembic_version')") is None
                # Older saved configurations also default to enabled when the flag is absent.
                document["environment"].pop("EVERUNFOLD_AUTO_INIT_EMPTY_DB")
                prepared = invoke("--prepare")
                assert prepared.returncode == 0, prepared.stdout + prepared.stderr
                assert "[初始化 3/3]" in prepared.stdout
                assert (
                    await db.fetchval("SELECT version_num FROM alembic_version") == SCHEMA_REVISION
                )
                password_hash = await db.fetchval(
                    f'SELECT password_hash FROM "{User.__tablename__}" WHERE username = $1', "admin"
                )
                assert verify_password(password_hash, initial_password)
                # The schema may be current while another initializer still holds its lock.
                await db.fetchval("SELECT pg_advisory_lock($1)", INITIALIZE_LOCK)
                busy = invoke("--prepare")
                assert busy.returncode == 1 and "仍在完成数据库初始化" in busy.stdout
                await db.fetchval("SELECT pg_advisory_unlock($1)", INITIALIZE_LOCK)
                await db.execute("CREATE TABLE preserved_example(value integer)")
                await db.execute("INSERT INTO preserved_example VALUES(42)")
                document["environment"]["EVERUNFOLD_ADMIN_PASSWORD"] = (
                    "must-not-change-existing-password"
                )
                for action in ("--prepare", "--initialize"):
                    repeated = invoke(action)
                    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
                    assert "[初始化 1/3]" not in repeated.stdout
                assert (
                    await db.fetchval(
                        f'SELECT password_hash FROM "{User.__tablename__}" WHERE username = $1',
                        "admin",
                    )
                    == password_hash
                )
                assert await db.fetchval("SELECT value FROM preserved_example") == 42
                await db.execute("UPDATE alembic_version SET version_num='old-test-revision'")
                assert invoke("--prepare").returncode == 1
                assert (
                    await db.fetchval("SELECT version_num FROM alembic_version")
                    == "old-test-revision"
                )
                await db.execute("DROP TABLE alembic_version")
                unmanaged = invoke("--prepare")
                assert unmanaged.returncode == 1 and "已有对象" in unmanaged.stdout
                assert await db.fetchval("SELECT value FROM preserved_example") == 42
                assert await db.fetchval("SELECT to_regclass('alembic_version')") is None
            finally:
                await db.close()
        finally:
            if created:
                assert name.startswith("everunfold_deploy_test_")
                await owner.execute(f'DROP DATABASE "{name}"')
            await owner.close()

    asyncio.run(run())
