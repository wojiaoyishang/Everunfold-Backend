"""Extend the real two-API collaboration test with online/offline merge races."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from pycrdt import Doc
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

from app.modules.content.collaboration.document import project
from tests.test_auth_http_processes import ORIGIN
from tests.test_collaboration_processes import Room


async def verify_versions(
    client: httpx.Client,
    csrf: dict[str, str],
    api_a: str,
    api_b: str,
    sql: Callable[..., Awaitable[Any]],
    connections: list[ClientConnection],
) -> None:
    prefix = "/v1/admin/content/"
    block_id = str(uuid4())
    fields = {
        "title": "合并并发验收",
        "body": [{"id": block_id, "type": "markdown", "content": "第一行\n第二行\n第三行\n"}],
    }
    created = client.post(
        prefix + "create",
        json={"operation_id": str(uuid4()), "type_id": "article", "fields": fields},
        headers=csrf,
    ).json()["data"]
    identity = created["id"]

    def post(
        path: str, payload: dict[str, Any], *, api: str = api_a, expected: int = 200
    ) -> dict[str, Any]:
        response = client.post(api + prefix + path, json={"id": identity, **payload}, headers=csrf)
        assert response.status_code == expected, (path, response.text)
        return dict(response.json().get("data") or response.json())

    async def room(api: str, number: int) -> Room:
        ticket = post("collaboration/ticket", {"source_first": True}, api=api)
        socket = await connect(
            api.replace("http:", "ws:") + ticket["ws_path"], origin=Origin(ORIGIN)
        )
        connections.append(socket)
        doc: Doc[Any] = Doc(client_id=number)
        await socket.send(
            json.dumps(
                {
                    "type": "auth",
                    "ticket": ticket["ticket"],
                    "client_id": number,
                    "state_vector": base64.b64encode(doc.get_state()).decode(),
                }
            )
        )
        instance = Room(socket, doc, ticket["epoch"])
        await instance.receive("sync")
        return instance

    online, offline = await room(api_a, 2101), await room(api_b, 2102)
    base_seq, base_epoch = offline.seq, offline.epoch
    lost_ack = offline.edit(block_id, "已保存但确认丢失\n")
    assert await offline.send(lost_ack) == 1
    # The offline branch only remembers its old base; an accepted receipt advances
    # that base selectively, preventing its already-saved insertion appearing twice.
    local_fields = project(offline.doc).model_dump(mode="json")
    local_fields["body"][0]["content"] = local_fields["body"][0]["content"].replace(
        "第一行", "本地第一行"
    )
    branch_id = str(uuid4())
    upload = {
        "branch_id": branch_id,
        "base_epoch": base_epoch,
        "base_seq": base_seq,
        "fields": local_fields,
        "updates": [{"update_id": lost_ack["update_id"], "payload": lost_ack["payload"]}],
    }
    branch = post("versions/branch/upload", upload, api=api_b)
    assert branch["base"]["body"][0]["content"].count("已保存但确认丢失") == 1
    assert post("versions/branch/upload", upload)["branch"]["id"] == branch_id
    while online.seq < 1:
        await online.receive("update")
    edit = online.edit(block_id, "在线正在输入\n")
    # Compete through different API processes: the winning row lock defines the
    # exact merge target, with no lost accepted edits and no late writes to it.
    merge_id = str(uuid4())
    await online.socket.send(json.dumps(edit))
    merge = await asyncio.to_thread(
        post, "versions/merge/begin", {"branch_id": branch_id, "merge_id": merge_id}
    )
    async with asyncio.timeout(8):
        while True:
            event = json.loads(await online.socket.recv())
            if event["type"] == "ack" and event["update_id"] == edit["update_id"]:
                assert merge["target_seq"] >= event["committed_seq"]
                assert "在线正在输入" in merge["remote"]["body"][0]["content"]
                break
            if event["type"] == "error":
                assert event["code"] == 40980
                assert "在线正在输入" not in merge["remote"]["body"][0]["content"]
                break
    assert merge["result"]["body"][0]["content"].count("已保存但确认丢失") == 1
    newer = online.edit(block_id, "锁定后输入仍在本地\n")
    await online.socket.send(json.dumps(newer))
    assert (await online.receive("error"))["code"] == 40980
    for path, payload in [
        (
            "collaboration/commit",
            {
                "operation_id": str(uuid4()),
                "expected_revision": 1,
                "epoch": online.epoch,
                "committed_seq": merge["target_seq"],
            },
        ),
        ("save", {"operation_id": str(uuid4()), "expected_revision": 1, "fields": fields}),
        ("trash", {"operation_id": str(uuid4()), "expected_revision": 1}),
        ("collaboration/source/acquire", {"block_id": block_id, "client_id": 2101}),
        ("versions/merge/begin", {"branch_id": branch_id, "merge_id": str(uuid4())}),
    ]:
        assert post(path, payload, expected=409)["code"] in {40972, 40980}
    post("versions/merge/renew", {"merge_id": merge_id})
    post("versions/merge/cancel", {"merge_id": merge_id})
    assert post("versions/list", {})["lock"] is None
    merge_id = str(uuid4())
    merge = post("versions/merge/begin", {"branch_id": branch_id, "merge_id": merge_id})
    if merge["conflicts"]:
        post(
            "versions/merge/resolve",
            {
                "merge_id": merge_id,
                "resolutions": [
                    {"key": conflict["key"], "choice": "local"} for conflict in merge["conflicts"]
                ],
            },
        )
    await sql(
        "ALTER TABLE content_draft_revisions ADD CONSTRAINT fail_merge "
        "CHECK (action <> 'merge') NOT VALID"
    )
    post("versions/merge/finish", {"merge_id": merge_id}, expected=503)
    assert post("versions/list", {})["lock"]["id"] == merge_id
    await sql("ALTER TABLE content_draft_revisions DROP CONSTRAINT fail_merge")
    finished = post("versions/merge/finish", {"merge_id": merge_id}, api=api_b)
    assert finished["status"] == "completed" and finished["result_revision"] == 2
    assert post("versions/merge/finish", {"merge_id": merge_id}) == finished
    current = post("detail", {})
    assert current["body"][0]["content"].count("已保存但确认丢失") == 1
    assert "本地第一行" in current["body"][0]["content"]
    assert post("versions/delete", {"revision": 2}, expected=409)["code"] == 40982
    post("versions/delete", {"revision": 1})
    assert post("versions/list", {})["total"] == 1
    # Old-epoch outboxes cannot leak into the new latest version.
    await offline.socket.send(json.dumps(lost_ack))
    assert (await offline.receive("error"))["code"] == 40970
    fresh = await room(api_b, 2103)
    assert fresh.epoch != base_epoch
    # Retained old epoch still permits recovery after a deleted history entry.
    post("versions/branch/upload", {**upload, "branch_id": str(uuid4())})
    expired_id = str(uuid4())
    post("versions/merge/begin", {"branch_id": branch_id, "merge_id": expired_id})
    await sql(
        "UPDATE content_draft_merges SET expires_at=now()-interval '1 second' WHERE id=$1",
        __import__("uuid").UUID(expired_id),
    )
    post("versions/merge/finish", {"merge_id": expired_id}, expected=409)
    assert post("versions/list", {})["lock"] is None
    assert await fresh.send(fresh.edit(block_id, "租约过期后可继续编辑\n")) > merge["target_seq"]
