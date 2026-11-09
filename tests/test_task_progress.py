"""Resumable monitoring with real PostgreSQL, separate writers and ownership fences."""

import asyncio
import os
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.core.config import CORSConfig, load_settings
from app.core.database import Database
from app.core.jobs.models import TaskRun
from app.core.jobs.progress import progress_scope, report_progress, task_progress, watch_progress
from app.core.jobs.progress_models import TaskProgressEventRecord as Event
from app.core.jobs.progress_schemas import WatchTask
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.settings.mail_test_service import MailTestService
from app.modules.settings.models import MailTestDelivery
from app.modules.settings.service import SettingsService
from tests.test_accounts import ORIGIN, PASSWORD, headers, signed_in
from tests.test_settings_processes import toml_text


@pytest.mark.skipif(os.getenv("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database")
def test_ordered_long_poll_permissions_and_stale_reporters(tmp_path):
    root = Path(__file__).resolve().parents[1]
    base_config = load_settings(root / "config.test.local.toml")
    base = make_url(base_config.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_progress_test_" + uuid4().hex[:10]
    raw = tomllib.loads((root / "config.test.local.toml").read_text())
    raw["database"]["url"] = base.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "progress-test-" + uuid4().hex
    raw["frontends"] = {}
    path = tmp_path / "config.toml"
    path.write_text(toml_text(raw))
    env = {**os.environ, "CONFIG_FILE": str(path)}
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "EVERUNFOLD_ADMIN_PASSWORD",
        "EVERUNFOLD_ADMIN_USERNAME",
    ):
        env.pop(key, None)
    config = load_settings(path)
    user, identity, mail = uuid4(), uuid4(), uuid4()
    actor = Actor(
        user, "tester", "Tester", "user", {"settings:read": "all", "settings:write": "all"}
    )
    lease = datetime.now(UTC) + timedelta(minutes=5)
    try:
        subprocess.run(
            [sys.executable, "init.database.py", "--config", str(path)],
            env=env,
            cwd=root,
            check=True,
            capture_output=True,
        )

        async def scenario():
            db = Database(config.database.url.get_secret_value())
            try:
                async with db.session_factory() as session:
                    session.add(
                        TaskRun(
                            id=identity,
                            task_id="example.progress",
                            planned_at=datetime.now(UTC),
                            status="running",
                            owner="current",
                            lease_until=lease,
                            started_at=datetime.now(UTC),
                        )
                    )
                    session.add(
                        MailTestDelivery(
                            id=mail,
                            user_id=user,
                            revision=1,
                            recipient="reader@example.test",
                            status="sending",
                            created_at=datetime.now(UTC),
                            lease_until=lease,
                        )
                    )
                    await session.commit()
                # A stopped Worker cannot keep expired mail requests looking active forever.
                for stale_status, expected_state, expected_error in (
                    ("queued", "failed", "queue_timeout"),
                    ("sending", "unknown", "interrupted"),
                ):
                    stale_id = uuid4()
                    async with db.session_factory() as session:
                        session.add(
                            MailTestDelivery(
                                id=stale_id,
                                user_id=user,
                                revision=1,
                                recipient="reader@example.test",
                                status=stale_status,
                                created_at=datetime.now(UTC) - timedelta(minutes=11),
                                lease_until=datetime.now(UTC) - timedelta(seconds=1),
                            )
                        )
                        await session.commit()
                    started_wait = time.monotonic()
                    projected = await watch_progress(
                        config,
                        actor,
                        WatchTask(
                            resource="mail-test",
                            id=stale_id,
                            wait_seconds=20,
                        ),
                    )
                    assert projected.state == expected_state and projected.stage == expected_state
                    assert projected.percent is None and time.monotonic() - started_wait < 2
                    # The legacy status reader persists the same terminal interpretation.
                    async with db.session_factory() as session:
                        persisted = await MailTestService(SettingsService(session, config)).read(
                            stale_id, user
                        )
                    assert persisted.status == expected_state and persisted.error == expected_error
                    after = await watch_progress(
                        config,
                        actor,
                        WatchTask(
                            resource="mail-test",
                            id=stale_id,
                            wait_seconds=0,
                        ),
                    )
                    assert after.state == projected.state and after.finished_at is not None
                query = WatchTask(resource="runtime", id=identity, wait_seconds=0)
                initial = await watch_progress(config, actor, query)
                assert (
                    initial.percent is None and initial.stage == "running" and initial.events == []
                )
                assert not await task_progress(stage="outside", message="Not bound")
                assert not await report_progress(
                    "runtime",
                    identity,
                    config,
                    owner="old",
                    stage="stale",
                    message="Must be fenced",
                )
                with progress_scope(config, identity, "current"):
                    assert await task_progress(
                        stage="processing",
                        message="First batch",
                        completed=2,
                        total=4,
                        unit="items",
                    )
                first = await watch_progress(config, actor, query)
                assert first.percent == 50 and first.completed == 2 and first.total == 4
                assert first.events[0].message == "First batch"
                # A separate interpreter commits while a reader waits.
                code = f"""import asyncio
from uuid import UUID
from app.core.config import load_settings
from app.core.jobs.progress import report_progress
asyncio.run(report_progress(
    'runtime', UUID('{identity}'), load_settings(), owner='current',
    stage='response', kind='response', message='Committed by another process'))
"""
                pending = asyncio.create_task(
                    watch_progress(
                        config,
                        actor,
                        query.model_copy(
                            update={"after_sequence": first.cursor, "wait_seconds": 5}
                        ),
                    )
                )
                await asyncio.sleep(0.1)
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    code,
                    env=env,
                    cwd=root,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
                assert process.returncode == 0
                resumed = await pending
                assert resumed.cursor > first.cursor and len(resumed.events) == 1
                assert (
                    resumed.events[0].message == "Committed by another process"
                    and resumed.percent == 50
                )
                # Concurrent writers commit ordered sequence values for this same resource.
                await asyncio.gather(
                    *(
                        report_progress(
                            "runtime",
                            identity,
                            config,
                            owner="current",
                            stage="batch",
                            message=f"event {index}",
                        )
                        for index in range(6)
                    )
                )
                page = await watch_progress(config, actor, query)
                assert [event.sequence for event in page.events] == sorted(
                    {event.sequence for event in page.events}
                )
                assert len(page.events) == 8
                before = time.monotonic()
                empty = await watch_progress(
                    config,
                    actor,
                    query.model_copy(update={"after_sequence": page.cursor, "wait_seconds": 1}),
                )
                assert not empty.events and time.monotonic() - before >= 0.8
                cancelled = asyncio.create_task(
                    watch_progress(
                        config,
                        actor,
                        query.model_copy(
                            update={"after_sequence": page.cursor, "wait_seconds": 20}
                        ),
                    )
                )
                await asyncio.sleep(0.1)
                cancelled.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled
                readonly = Actor(user, "tester", "Tester", "user", {"settings:read": "all"})
                with pytest.raises(AuthFailure) as forbidden:
                    await watch_progress(
                        config, readonly, WatchTask(resource="mail-test", id=mail, wait_seconds=0)
                    )
                assert forbidden.value.status == 403
                other = Actor(uuid4(), "other", "Other", "user", actor.permissions)
                with pytest.raises(AuthFailure) as missing:
                    await watch_progress(
                        config, other, WatchTask(resource="mail-test", id=mail, wait_seconds=0)
                    )
                assert missing.value.status == 404
                assert not await report_progress(
                    "mail-test",
                    mail,
                    config,
                    stage="auth",
                    message="stale thread",
                    expected_lease=lease - timedelta(seconds=1),
                )
                assert await report_progress(
                    "mail-test",
                    mail,
                    config,
                    stage="auth",
                    message="Credentials redacted",
                    expected_lease=lease,
                )
                async with db.session_factory() as session:
                    row = await session.get(TaskRun, identity)
                    row.status, row.finished_at = "succeeded", datetime.now(UTC)
                    await session.commit()
                before = time.monotonic()
                terminal = await watch_progress(
                    config,
                    actor,
                    query.model_copy(update={"after_sequence": page.cursor, "wait_seconds": 20}),
                )
                assert terminal.state == "succeeded" and time.monotonic() - before < 2
                assert not await report_progress(
                    "runtime",
                    identity,
                    config,
                    owner="current",
                    stage="late",
                    message="Must not append",
                )
                # Bulk seed real retention/page boundaries without a thousand network calls.
                async with db.session_factory() as session:
                    for _ in range(1100):
                        session.add(
                            Event(
                                resource="mail-test",
                                resource_id=mail,
                                at=datetime.now(UTC),
                                level="info",
                                kind="message",
                                message="older event",
                                stage="auth",
                            )
                        )
                    await session.commit()
                assert await report_progress(
                    "mail-test",
                    mail,
                    config,
                    stage="auth",
                    message="X" * 9000,
                    expected_lease=lease,
                )
                async with db.session_factory() as session:
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(Event).where(Event.resource_id == mail)
                        )
                        == 1000
                    )
                seen = []
                after = 0
                while True:
                    batch = await watch_progress(
                        config,
                        actor,
                        WatchTask(
                            resource="mail-test", id=mail, after_sequence=after, wait_seconds=0
                        ),
                    )
                    seen.extend(batch.events)
                    after = batch.cursor
                    if not batch.has_more:
                        break
                assert len(seen) == 1000 and len({event.sequence for event in seen}) == 1000
                assert len(seen[-1].message) == 8192
            finally:
                await db.close()

        asyncio.run(scenario())
        # Exercise actual authentication, API-key grant ceilings and revocation at the API boundary.
        api_config = config.model_copy(update={"cors": CORSConfig(allowed_origins=[ORIGIN])})
        admin_name = "progress_" + uuid4().hex
        asyncio.run(seed_admin(api_config, admin_name, PASSWORD))
        with TestClient(
            create_app(api_config), client=("progress-test-" + uuid4().hex, 50000)
        ) as client:
            signed_in(client, admin_name)
            watch = {"resource": "runtime", "id": str(identity), "wait_seconds": 0}
            assert (
                client.post(
                    "/v1/admin/tasks/watch", json=watch, headers=headers(client)
                ).status_code
                == 200
            )
            response = client.post(
                "/v1/account/api-keys",
                json={
                    "operation_id": str(uuid4()),
                    "app_id": "everunfold",
                    "name": "Progress reader",
                    "permissions": {"settings:read": "all"},
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "current_password": PASSWORD,
                },
                headers=headers(client),
            )
            assert response.status_code == 201, response.text
            key = response.json()["data"]
            authorization = {"Authorization": "Bearer " + key["secret"]}
            assert (
                client.post("/v1/admin/tasks/watch", json=watch, headers=authorization).status_code
                == 200
            )
            assert (
                client.post(
                    "/v1/admin/tasks/watch",
                    json={**watch, "resource": "mail-test", "id": str(mail)},
                    headers=authorization,
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/v1/account/api-keys/revoke",
                    json={
                        "operation_id": str(uuid4()),
                        "key_id": key["key"]["id"],
                    },
                    headers=headers(client),
                ).status_code
                == 200
            )
            assert (
                client.post("/v1/admin/tasks/watch", json=watch, headers=authorization).status_code
                == 401
            )

    finally:

        async def cleanup():
            connection = await asyncpg.connect(
                base.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(cleanup())
