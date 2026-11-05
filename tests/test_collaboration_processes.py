"""P0: two real API processes, PostgreSQL commits, Redis presence and dropped ACK replay."""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from pycrdt import Doc, Map, XmlElement
from sqlalchemy.engine import make_url
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.models import AuthAuditEvent
from app.modules.auth.security import token_digest
from app.modules.content.collaboration.document import project
from app.modules.content.collaboration.service import authorize
from app.modules.media.locking import lock_references
from tests.test_auth_http_processes import ORIGIN, free_port, uvicorn_process
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/v1/admin/content/"


class Room:
    def __init__(self, socket: ClientConnection, doc: Doc[Any], epoch: str) -> None:
        self.socket, self.doc, self.epoch = socket, doc, epoch
        self.seq = 0
        self.revisions: list[tuple[int, int]] = []

    async def receive(self, kind: str, *, update_id: str | None = None) -> dict[str, Any]:
        async with asyncio.timeout(8):
            while True:
                value = json.loads(await self.socket.recv())
                if value["type"] == "revision":
                    self.revisions.append((value["revision"], value["committed_seq"]))
                if value["type"] in {"sync", "update"}:
                    self.doc.apply_update(base64.b64decode(value["payload"]))
                    self.seq = value["committed_seq"]
                if value["type"] == kind and (not update_id or value.get("update_id") == update_id):
                    return dict(value)
                assert value["type"] != "error", value

    def edit(self, block_id: str, value: str) -> dict[str, Any]:
        vector = self.doc.get_state()
        block = self.doc.get("blocks", type=Map)[block_id]
        if "rich" in block:
            block["rich"].children.append(XmlElement("rawHtml", attributes={"html": value}))
            # Structural edits from the real SDK also carry append-only position history.
            client = self.doc.client_id
            history = self.doc.get(f"__tiptap_position_migrations:item:{client}:0", type=Map)
            clock = 1000 + len(history)
            history[f"{client}:{clock}"] = {
                "version": 1,
                "client": client,
                "clock": clock,
                "seen": [[client, clock]],
                "transfers": [
                    {"source": {"client": client, "clock": 1, "length": 1}, "target": None}
                ],
            }
        else:
            text = block["content"]
            text.insert(len(text), value)
        return {
            "type": "update",
            "epoch": self.epoch,
            "update_id": str(uuid4()),
            "payload": base64.b64encode(self.doc.get_update(vector)).decode(),
            "rich_versions": {block_id: 0} if "rich" in block else {},
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
                    # The permission lock must coexist with legacy reference writers' audit FKs.
                    db = Database(url.render_as_string(hide_password=False))
                    try:
                        async with db.session_factory() as legacy, db.session_factory() as editing:
                            await lock_references(legacy)
                            authorized = asyncio.Event()

                            async def wait_to_write() -> None:
                                await authorize(
                                    editing,
                                    token_digest(
                                        client.cookies.get(original.auth.cookie_name) or ""
                                    ),
                                    lock=True,
                                )
                                authorized.set()
                                await lock_references(editing)
                                await editing.commit()

                            writer = asyncio.create_task(wait_to_write())
                            try:
                                async with asyncio.timeout(3):
                                    await authorized.wait()
                                    legacy.add(
                                        AuthAuditEvent(
                                            user_id=UUID(authenticated["user"]["id"]),
                                            action="collab-lock-proof",
                                            created_at=datetime.now(UTC),
                                        )
                                    )
                                    await legacy.flush()
                                    await legacy.commit()
                                    await writer
                            finally:
                                writer.cancel()
                                await asyncio.gather(writer, return_exceptions=True)
                    finally:
                        await db.close()
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
                    # Real font parsing and hash reuse across replicas share the upload lifecycle.
                    from tests.test_fonts import font_bytes

                    font_data = font_bytes("woff2")
                    font_upload = client.post(
                        "/v1/admin/media/upload",
                        params={
                            "filename": "proof.woff2",
                            "upload_id": str(uuid4()),
                            "purpose": "font",
                        },
                        headers={**csrf, "Content-Type": "application/octet-stream"},
                        content=font_data,
                    )
                    assert font_upload.status_code == 200, font_upload.text
                    font_identity = font_upload.json()["data"]["id"]
                    reused = client.post(
                        api_b + "/v1/admin/media/upload",
                        params={
                            "filename": "proof-again.woff2",
                            "upload_id": str(uuid4()),
                            "purpose": "font",
                        },
                        headers={**csrf, "Content-Type": "application/octet-stream"},
                        content=font_data,
                    )
                    assert (
                        reused.status_code == 200
                        and reused.json()["data"]["id"] == font_identity
                        and reused.json()["data"]["reused"]
                    )
                    font_catalog = client.post(
                        api_b + "/v1/admin/fonts/query", json={"query": font_identity}, headers=csrf
                    ).json()["data"]
                    assert font_catalog["items"][0]["info"]["family"] == "Everunfold Test"
                    assert font_identity[6:] in client.get("/v1/fonts.css").text
                    # Source ownership is durable across replicas, independent of awareness.
                    acquired = client.post(
                        PREFIX + "collaboration/source/acquire",
                        json={"id": identity, "block_id": second, "client_id": 1003},
                        headers=csrf,
                    )
                    assert acquired.status_code == 200, acquired.text
                    source_lease = acquired.json()["data"]
                    denied = client.post(
                        api_b + PREFIX + "collaboration/source/acquire",
                        json={"id": identity, "block_id": second, "client_id": 1004},
                        headers=csrf,
                    )
                    assert denied.status_code == 409 and denied.json()["code"] == 40974
                    other_block = old.edit(first, "源码期间的其他区块输入")
                    assert await old.send(other_block) == 7
                    rejected = b.edit(second, "不应覆盖源码的输入")
                    await b.socket.send(json.dumps(rejected))
                    assert (await b.receive("error"))["code"] == 40974
                    assert "不应覆盖" in project(b.doc).body[1].content
                    source_html = (
                        f'<h2>源码应用</h2><p><span data-font="site://media/{font_identity[6:]}" '
                        f'style="font-family:ef-font-{font_identity[6:]}">字体内容</span></p>'
                    )
                    application = {
                        "id": identity,
                        "block_id": second,
                        "lease_id": source_lease["lease_id"],
                        "operation_id": str(uuid4()),
                        "expected_hash": source_lease["expected_hash"],
                        "html": source_html,
                    }
                    # A failed PostgreSQL write rolls back the document, receipt and generation.
                    await sql(
                        "ALTER TABLE collaboration_updates ADD CONSTRAINT fail_source "
                        "CHECK (committed_seq < 8)"
                    )
                    failed_source = client.post(
                        api_b + PREFIX + "collaboration/source/apply",
                        json=application,
                        headers=csrf,
                    )
                    assert failed_source.status_code == 503
                    assert (await sql("SELECT committed_seq FROM collaboration_documents"))[0][
                        "committed_seq"
                    ] == 7
                    assert (await sql("SELECT generation FROM collaboration_source_leases"))[0][
                        "generation"
                    ] == 0
                    await sql("ALTER TABLE collaboration_updates DROP CONSTRAINT fail_source")
                    applied = client.post(
                        api_b + PREFIX + "collaboration/source/apply",
                        json=application,
                        headers=csrf,
                    )
                    assert applied.status_code == 200, applied.text
                    assert (
                        applied.json()["data"]["committed_seq"] == 8
                        and applied.json()["data"]["generation"] == 1
                    )
                    assert (
                        client.post(
                            PREFIX + "collaboration/source/apply", json=application, headers=csrf
                        ).json()
                        == applied.json()
                    )
                    b = await room(api_b, 1005)
                    assert "源码应用" in project(b.doc).body[1].content
                    while old.seq < 8:
                        await old.receive("update")
                    assert project(old.doc).body == project(b.doc).body
                    # Stale offline HTML edits retain their local text and are never silently ACKed.
                    await b.socket.send(json.dumps(rejected))
                    stale = await b.receive("error")
                    assert stale["code"] == 40975
                    assert (await sql("SELECT committed_seq FROM collaboration_documents"))[0][
                        "committed_seq"
                    ] == 8
                    b = await room(api_b, 1006)
                    expired_lease = client.post(
                        PREFIX + "collaboration/source/acquire",
                        json={"id": identity, "block_id": second, "client_id": 1006},
                        headers=csrf,
                    ).json()["data"]
                    await sql(
                        "UPDATE collaboration_source_leases "
                        "SET expires_at=now()-interval '1 second'"
                    )
                    expired_payload = {
                        **application,
                        "operation_id": str(uuid4()),
                        "lease_id": expired_lease["lease_id"],
                        "expected_hash": expired_lease["expected_hash"],
                    }
                    assert (
                        client.post(
                            PREFIX + "collaboration/source/apply",
                            json=expired_payload,
                            headers=csrf,
                        ).status_code
                        == 409
                    )
                    # A font referenced by an unsaved collaborative draft cannot be removed.
                    font_detail = client.post(
                        "/v1/admin/media/detail", json={"id": font_identity}, headers=csrf
                    ).json()["data"]
                    removal = client.post(
                        "/v1/admin/media/remove-preview",
                        json={"id": font_identity, "expected_revision": font_detail["revision"]},
                        headers=csrf,
                    )
                    assert removal.status_code == 409, removal.text
                    # One formal save writes exact HTML and Markdown into the draft and
                    # into the same durable update received through the other API.
                    before = project(b.doc)
                    html = "<h2>保留 &amp; 源码</h2>\n\n<p>  缩进  </p>"
                    markdown = "## 同步验证\n\n* 保留 Markdown 空行"
                    block_sources = [
                        {
                            "id": str(block.id),
                            "type": block.type,
                            "base_hash": hashlib.sha256(block.content.encode()).hexdigest(),
                            "content": source,
                        }
                        for block, source in zip(before.body, (markdown, html), strict=True)
                    ]
                    save_sources = {
                        **commit,
                        "operation_id": str(uuid4()),
                        "expected_revision": 2,
                        "committed_seq": b.seq,
                        "action": "save",
                        "block_sources": block_sources,
                    }
                    await sql(
                        "ALTER TABLE content_draft_revisions ADD CONSTRAINT fail_source_commit "
                        "CHECK (revision <> 3) NOT VALID"
                    )
                    failed_save = client.post(
                        api_b + PREFIX + "collaboration/commit", json=save_sources, headers=csrf
                    )
                    assert failed_save.status_code == 503
                    assert (await sql("SELECT committed_seq FROM collaboration_documents"))[0][
                        "committed_seq"
                    ] == b.seq
                    await sql(
                        "ALTER TABLE content_draft_revisions DROP CONSTRAINT fail_source_commit"
                    )
                    source_save = client.post(
                        api_b + PREFIX + "collaboration/commit", json=save_sources, headers=csrf
                    )
                    assert source_save.status_code == 200, source_save.text
                    saved = source_save.json()["data"]
                    assert saved["committed_seq"] == 9
                    assert [part["content"] for part in saved["draft"]["body"]] == [markdown, html]
                    assert (
                        client.post(
                            api_a + PREFIX + "collaboration/commit", json=save_sources, headers=csrf
                        ).json()
                        == source_save.json()
                    )
                    await old.receive("update", update_id=save_sources["operation_id"])
                    assert (3, 9) in old.revisions
                    assert [block.content for block in project(old.doc).body] == [markdown, html]
                    conflict = client.post(
                        api_a + PREFIX + "collaboration/commit",
                        json={
                            **save_sources,
                            "operation_id": str(uuid4()),
                            "expected_revision": 3,
                        },
                        headers=csrf,
                    )
                    assert conflict.status_code == 409 and conflict.json()["code"] == 40976
                    assert (await sql("SELECT committed_seq FROM collaboration_documents"))[0][
                        "committed_seq"
                    ] == 9
                    # Session revocation closes idle sockets, and prevents fresh tickets.
                    from tests.version_process_checks import verify_versions

                    await verify_versions(client, csrf, api_a, api_b, sql, connections)
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
