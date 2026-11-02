"""P0: two real API processes, PostgreSQL commits, Redis presence and dropped ACK replay."""

import asyncio
import base64
import json
import os
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from pycrdt import Doc, Map
from sqlalchemy.engine import make_url
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

from app.core.config import load_settings
from app.modules.content.collaboration.document import project
from tests.test_auth_http_processes import ORIGIN, free_port, uvicorn_process
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/v1/admin/content/"


class Room:
    def __init__(self, socket: ClientConnection, doc: Doc[Any], epoch: str) -> None:
        self.socket, self.doc, self.epoch = socket, doc, epoch
        self.seq = 0

    async def receive(self, kind: str, *, update_id: str | None = None) -> dict[str, Any]:
        async with asyncio.timeout(8):
            while True:
                value = json.loads(await self.socket.recv())
                if value["type"] in {"sync", "update"}:
                    self.doc.apply_update(base64.b64decode(value["payload"]))
                    self.seq = value["committed_seq"]
                if value["type"] == kind and (not update_id or value.get("update_id") == update_id):
                    return dict(value)
                assert value["type"] != "error", value

    def edit(self, block_id: str, value: str) -> dict[str, Any]:
        vector = self.doc.get_state()
        text = self.doc.get("blocks", type=Map)[block_id]["content"]
        text.insert(len(text), value)
        return {
            "type": "update",
            "epoch": self.epoch,
            "update_id": str(uuid4()),
            "payload": base64.b64encode(self.doc.get_update(vector)).decode(),
        }

    async def send(self, update: dict[str, Any]) -> int:
        await self.socket.send(json.dumps(update))
        return int((await self.receive("ack", update_id=update["update_id"]))["committed_seq"])


@pytest.mark.skipif(os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Explicit real DB tests")
def test_collaboration_replicas_durable_ack_and_cutoff(tmp_path: Path) -> None:
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    database_name = "everunfold_collab_test_" + uuid4().hex[:12]
    url = original_url.set(database=database_name)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text(encoding="utf-8"))
    raw["database"]["url"] = url.render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-collab-" + uuid4().hex[:10]
    raw["frontends"] = {}
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
    config_file = tmp_path / "collab.local.toml"
    config_file.write_text(toml_text(raw), encoding="utf-8")
    environment = {**os.environ, "CONFIG_FILE": str(config_file), "PYTHONIOENCODING": "utf-8"}
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "EVERUNFOLD_ADMIN_PASSWORD",
        "EVERUNFOLD_ADMIN_USERNAME",
    ):
        environment.pop(key, None)

    async def sql(statement: str, *args: Any) -> Any:
        connection = await asyncpg.connect(dsn)
        try:
            return await connection.fetch(statement, *args)
        finally:
            await connection.close()

    try:
        result = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            timeout=40,
        )
        assert result.returncode == 0, "Isolated collaboration database initialization failed"
        port_a, port_b = free_port(), free_port()
        api_a, api_b = f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"
        with ExitStack() as stack:
            stack.enter_context(uvicorn_process(port_a, environment))
            stack.enter_context(uvicorn_process(port_b, environment))
            client = stack.enter_context(
                httpx.Client(base_url=api_a, headers={"Origin": ORIGIN}, timeout=10)
            )
            pre = client.get("/v1/auth/session").json()["data"]
            authenticated = client.post(
                "/v1/auth/login",
                json={"username": "admin", "password": "123456"},
                headers={"X-CSRF-Token": pre["csrf_token"]},
            ).json()["data"]
            csrf = {"X-CSRF-Token": authenticated["csrf_token"]}
            first, second = str(uuid4()), str(uuid4())
            fields = {
                "title": "Collaborative proof 中文",
                "body": [
                    {"id": first, "type": "markdown", "content": "初始"},
                    {"id": second, "type": "html", "content": "<p>HTML</p>"},
                ],
            }
            created = client.post(
                PREFIX + "create",
                json={"operation_id": str(uuid4()), "type_id": "article", "fields": fields},
                headers=csrf,
            )
            assert created.status_code == 200, created.text
            identity = created.json()["data"]["id"]

            def issue(api: str) -> dict[str, Any]:
                response = client.post(
                    api + PREFIX + "collaboration/ticket", json={"id": identity}, headers=csrf
                )
                assert response.status_code == 200, response.text
                return dict(response.json()["data"])

            async def check() -> None:
                connections: list[ClientConnection] = []

                async def room(api: str, number: int, state: bytes | None = None) -> Room:
                    data = issue(api)
                    doc: Doc[Any] = Doc(client_id=number)
                    if state:
                        doc.apply_update(state)
                    socket = await connect(
                        api.replace("http:", "ws:") + data["ws_path"], origin=Origin(ORIGIN)
                    )
                    connections.append(socket)
                    await socket.send(
                        json.dumps(
                            {
                                "type": "auth",
                                "ticket": data["ticket"],
                                "client_id": number,
                                "state_vector": base64.b64encode(doc.get_state()).decode(),
                            }
                        )
                    )
                    instance = Room(socket, doc, data["epoch"])
                    await instance.receive("sync")
                    return instance

                try:
                    a, b = await room(api_a, 1001), await room(api_b, 1002)
                    assert a.epoch == b.epoch
                    initial = a.doc.get_update()
                    one, two = a.edit(first, "甲🌟"), b.edit(first, "乙👨‍👩‍👧")
                    seqs = await asyncio.gather(a.send(one), b.send(two))
                    assert sorted(seqs) == [1, 2]
                    # No reliance on socket memory: both replicas catch up from committed log.
                    while a.seq < 2:
                        await a.receive("update")
                    while b.seq < 2:
                        await b.receive("update")
                    assert project(a.doc).body == project(b.doc).body
                    assert all(
                        word in project(a.doc).body[0].content for word in ("甲🌟", "乙👨‍👩‍👧")
                    )
                    assert (
                        await a.send(one) == seqs[0]
                    )  # Lost ACK / duplicate does not allocate seq.
                    assert (await sql("SELECT count(*) AS n FROM collaboration_updates"))[0][
                        "n"
                    ] == 2
                    assert (
                        client.post(
                            PREFIX + "save",
                            json={
                                "id": identity,
                                "operation_id": str(uuid4()),
                                "expected_revision": 1,
                                "fields": fields,
                            },
                            headers=csrf,
                        ).status_code
                        == 409
                    )
                    peers = await b.receive("peers")
                    assert any(
                        p["client_id"] == 1002 and p["name"] == "admin" for p in peers["peers"]
                    )
                    # A PostgreSQL failure cannot issue an ACK or consume the update ID.
                    await sql(
                        "ALTER TABLE collaboration_updates ADD CONSTRAINT fail_collab "
                        "CHECK (committed_seq < 3)"
                    )
                    failed = b.edit(second, "失败重试")
                    await b.socket.send(json.dumps(failed))
                    failure = await b.receive("error")
                    assert failure["code"] == 50370
                    assert (await sql("SELECT committed_seq FROM collaboration_documents"))[0][
                        "committed_seq"
                    ] == 2
                    await sql("ALTER TABLE collaboration_updates DROP CONSTRAINT fail_collab")
                    b = await room(api_b, 1003, b.doc.get_update())
                    assert await b.send(failed) == 3
                    while b.seq < 3:
                        await b.receive("update")
                    cutoff = b.seq
                    later = b.edit(first, "尚未发布的后续输入")
                    assert await b.send(later) == 4
                    # Freeze N while N+1 already exists. Never publish the newer mutable head.
                    commit = {
                        "id": identity,
                        "operation_id": str(uuid4()),
                        "expected_revision": 1,
                        "epoch": b.epoch,
                        "committed_seq": cutoff,
                        "action": "publish",
                    }
                    published = client.post(
                        PREFIX + "collaboration/commit", json=commit, headers=csrf
                    )
                    assert published.status_code == 200, published.text
                    assert published.json()["data"]["committed_seq"] == 3
                    assert "尚未发布" not in json.dumps(
                        published.json()["data"]["draft"]["body"], ensure_ascii=False
                    )
                    assert (
                        client.post(
                            PREFIX + "collaboration/commit", json=commit, headers=csrf
                        ).json()
                        == published.json()
                    )
                    # A stale checkpoint client recovers all edits, then duplicate receipts survive.
                    old = await room(api_a, 1004, initial)
                    assert old.seq == 4 and "尚未发布" in project(old.doc).body[0].content
                    assert await old.send(one) == seqs[0]
                    # Commit without Redis publish, emulating a replica dying after DB commit.
                    unseen = old.edit(second, "数据库补齐")
                    from app.core.database import Database
                    from app.modules.auth.security import token_digest
                    from app.modules.content.collaboration.schemas import ClientUpdate
                    from app.modules.content.collaboration.service import accept_update

                    db = Database(url.render_as_string(hide_password=False))
                    try:
                        async with db.session_factory() as session:
                            assert (
                                await accept_update(
                                    session,
                                    load_settings(config_file),
                                    UUID(identity),
                                    token_digest(
                                        client.cookies.get(original.auth.cookie_name) or ""
                                    ),
                                    ClientUpdate.model_validate(unseen),
                                )
                                == 5
                            )
                    finally:
                        await db.close()
                    while b.seq < 5:
                        await b.receive("update")
                    assert "数据库补齐" in project(b.doc).body[1].content
                    # New media addresses follow shared stable references without publishing first.
                    from io import BytesIO

                    from PIL import Image

                    image = BytesIO()
                    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
                    uploaded = client.post(
                        "/v1/admin/media/upload",
                        params={"filename": "collaboration-proof.png", "upload_id": str(uuid4())},
                        headers={**csrf, "Content-Type": "application/octet-stream"},
                        content=image.getvalue(),
                    )
                    assert uploaded.status_code == 200, uploaded.text
                    media_id = uploaded.json()["data"]["id"].removeprefix("media:")
                    attached = b.edit(second, f'<img src="site://media/{media_id}" alt="test">')
                    assert await b.send(attached) == 6
                    resolved = await old.receive("media")
                    assert media_id in resolved["media_urls"]
                    assert resolved["media_urls"][media_id]["original"].endswith(".png")
                    # Session revocation closes idle sockets, and prevents fresh tickets.
                    await sql("UPDATE users SET active=false WHERE username='admin'")
                    revoked = await b.receive("error")
                    assert revoked["code"] == 40101
                    assert (
                        client.post(
                            PREFIX + "collaboration/ticket", json={"id": identity}, headers=csrf
                        ).status_code
                        == 401
                    )
                finally:
                    for connection in connections:
                        await connection.close()

            asyncio.run(check())
    finally:
        assert database_name.startswith("everunfold_collab_test_")

        async def cleanup() -> None:
            connection = await asyncpg.connect(
                original_url.set(drivername="postgresql", database="postgres").render_as_string(
                    hide_password=False
                )
            )
            try:
                await connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1",
                    database_name,
                )
                await connection.execute('DROP DATABASE IF EXISTS "' + database_name + '"')
            finally:
                await connection.close()

        asyncio.run(cleanup())
