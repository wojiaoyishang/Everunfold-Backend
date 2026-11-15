"""Replica-safe socket transport. Database catch-up does not depend on Redis delivery."""

import asyncio
import base64
import colorsys
import hashlib
import json
import logging
import time
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.redis_keys import redis_channel, redis_key
from app.core.runtime import Runtime
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import decode, project
from app.modules.content.collaboration.models import CollaborationDocument, CollaborationUpdate
from app.modules.content.collaboration.schemas import ClientAuth, ClientUpdate, Peer, PresenceInput
from app.modules.content.collaboration.service import (
    accept_update,
    authorize,
    consume_ticket,
    draft_row,
)
from app.modules.content.collaboration.source_leases import source_states
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.merge_lock import lock_state
from app.modules.settings.browser_origins import managed_origin

logger = logging.getLogger(__name__)


def peer_color(actor_id: UUID, client_id: int) -> str:
    digest = hashlib.sha256(f"{actor_id}:{client_id}".encode()).digest()
    hue = int.from_bytes(digest[:3], "big") / 16_777_216
    saturation = 0.48 + digest[3] / 255 * 0.14
    lightness = 0.34 + digest[4] / 255 * 0.08
    return "#" + "".join(
        f"{round(channel * 255):02x}" for channel in colorsys.hls_to_rgb(hue, lightness, saturation)
    )


# Shared leases bound connections across API processes; presence is never authoritative.
LEASE = """
local owner=redis.call('GET',KEYS[2])
if owner and owner~=ARGV[4] then return 0 end
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1])
local existing = redis.call('ZSCORE',KEYS[1],ARGV[3])
if not existing and redis.call('ZCARD',KEYS[1]) >= 20 then return 0 end
redis.call('ZADD',KEYS[1],ARGV[2],ARGV[3]); redis.call('EXPIRE',KEYS[1],30)
redis.call('SET',KEYS[2],ARGV[4],'EX',20)
return 1
"""


async def redis_result(command: Any) -> Any:
    # redis-py's sync/async shared annotations expose a union for these commands.
    return await command


async def serve(socket: WebSocket, draft_id: UUID) -> None:
    runtime: Runtime | None = socket.app.state.runtime
    origin = socket.headers.get("origin")
    if (
        runtime is None
        or origin is None
        or (
            origin not in runtime.settings.cors.allowed_origins
            and origin != managed_origin(socket.scope)
        )
    ):
        await socket.close(code=1008)
        return
    await socket.accept()
    connection_id = uuid4()
    room = redis_key(runtime.settings, "collab", str(draft_id), "leases")
    peers_key = redis_key(runtime.settings, "collab", str(draft_id), "peers")
    channel = redis_channel(runtime.settings, "collab", str(draft_id), "updates")
    member = ""
    client_key = ""
    pubsub = runtime.redis.pubsub()
    send_lock = asyncio.Lock()

    async def send(value: dict[str, Any]) -> None:
        async with send_lock, asyncio.timeout(5):
            await socket.send_json(value)

    try:
        async with asyncio.timeout(8):
            auth = await socket.receive_json()
        authentication = ClientAuth.model_validate(auth)
        client_id = authentication.client_id
        client_key = redis_key(runtime.settings, "collab", str(draft_id), "client", str(client_id))
        async with runtime.database.session_factory() as session:
            session_hash, actor = await consume_ticket(session, draft_id, authentication.ticket)
        # One active connection per Y.Doc client ID: another tab creates its own client ID.
        member = f"{client_id}:{connection_id}"
        now = time.time()
        if not await redis_result(
            runtime.redis.eval(
                LEASE, 2, room, client_key, str(now), str(now + 20), member, str(connection_id)
            )
        ):
            raise AuthFailure(429, 42971, "同时编辑的人数已达上限，请稍后重试")
        peer = Peer(
            connection_id=connection_id,
            client_id=client_id,
            name=actor.display_name,
            color=peer_color(actor.id, client_id),
        )
        presence = PresenceInput()
        await pubsub.subscribe(channel)
        async with runtime.database.session_factory() as session:
            document = await session.get(CollaborationDocument, draft_id)
            draft = await draft_row(session, draft_id, blocks_only=True)
            assert document is not None
            seq, revision = document.committed_seq, draft.revision
            epoch = document.epoch
            last_references = document.media_ids
            fields = project(
                decode(document.state), EditorSettings.model_validate(draft.editor_settings)
            )
            media = await editor_media(
                session,
                runtime.settings,
                fields.model_dump_json(),
                fields.settings or EditorSettings(),
                strict=False,
            )
            await send(
                {
                    "type": "sync",
                    "epoch": str(document.epoch),
                    "committed_seq": seq,
                    "payload": base64.b64encode(
                        decode(document.state).get_update(
                            base64.b64decode(authentication.state_vector, validate=True)
                        )
                    ).decode(),
                    "revision": revision,
                    "media_urls": {key: value.model_dump() for key, value in media.items()},
                }
            )

        async def incoming() -> None:
            nonlocal presence
            window_start, messages = time.monotonic(), 0
            while True:
                raw = await socket.receive_text()
                if time.monotonic() - window_start > 5:
                    window_start, messages = time.monotonic(), 0
                messages += 1
                if messages > 300:
                    raise AuthFailure(429, 42970, "操作过于频繁，请稍后重试")
                if len(raw) > 1_500_000:
                    raise AuthFailure(413, 41370, "协同更新过大，请导出本地内容")
                value = json.loads(raw)
                if value.get("type") == "presence":
                    presence = PresenceInput.model_validate(value)
                    continue
                update = ClientUpdate.model_validate(value)
                async with runtime.database.session_factory() as session:
                    try:
                        committed = await accept_update(
                            session, runtime.settings, draft_id, session_hash, update
                        )
                    except AuthFailure as exc:
                        if exc.code not in {40970, 40980}:
                            raise
                        await session.rollback()
                        await send({"type": "error", "code": exc.code, "msg": exc.message})
                        continue
                # This point is reachable only after PostgreSQL commit; duplicates re-ACK same seq.
                await send(
                    {"type": "ack", "update_id": str(update.update_id), "committed_seq": committed}
                )
                with suppress(RedisError, OSError):
                    await runtime.redis.publish(
                        channel,
                        json.dumps(
                            {
                                "protocol": "collab-1",
                                "document_id": str(draft_id),
                                "update_id": str(update.update_id),
                                "committed_seq": committed,
                                "origin_instance_id": str(connection_id),
                            }
                        ),
                    )

        async def outgoing() -> None:
            nonlocal seq, revision, last_references, epoch
            last_presence = 0.0
            last_sources = ""
            last_control = ""
            last_checkpoint = time.monotonic()
            while True:
                async with runtime.database.session_factory() as session:
                    await authorize(session, session_hash, draft_id=draft_id)
                    draft = await draft_row(session, draft_id, blocks_only=True)
                    head = await session.get(CollaborationDocument, draft_id)
                    assert head is not None
                    locked = await lock_state(session, draft_id)
                    control = json.dumps(
                        locked.model_dump(mode="json") if locked else None, sort_keys=True
                    )
                    # Reset first on generation changes. An old client must never replay
                    # its outbox into the merged head, even if it missed the freeze frame.
                    if head.epoch != epoch:
                        epoch, seq = head.epoch, head.committed_seq
                        await send(
                            {
                                "type": "reset",
                                "epoch": str(epoch),
                                "committed_seq": seq,
                                "payload": base64.b64encode(head.state).decode(),
                                "revision": draft.revision,
                                "merge": locked.model_dump(mode="json") if locked else None,
                            }
                        )
                    if control != last_control:
                        last_control = control
                        await send(
                            {
                                "type": "control",
                                "epoch": str(epoch),
                                "committed_seq": head.committed_seq,
                                "merge": locked.model_dump(mode="json") if locked else None,
                            }
                        )
                    sources = [
                        item.model_dump(mode="json")
                        for item in await source_states(session, draft_id)
                    ]
                    serialized_sources = json.dumps(sources, sort_keys=True)
                    if serialized_sources != last_sources:
                        last_sources = serialized_sources
                        await send({"type": "sources", "sources": sources})
                    if draft.revision != revision:
                        revision = draft.revision
                        head = await session.get(CollaborationDocument, draft_id)
                        # The revision may reach the browser before its update batch.
                        # A save must wait for at least this durable document cutoff.
                        await send(
                            {
                                "type": "revision",
                                "revision": revision,
                                "committed_seq": head.committed_seq if head else seq,
                            }
                        )
                    updates = (
                        await session.scalars(
                            select(CollaborationUpdate)
                            .where(
                                CollaborationUpdate.draft_id == draft_id,
                                CollaborationUpdate.committed_seq > seq,
                            )
                            .order_by(CollaborationUpdate.committed_seq)
                            .limit(16)
                        )
                    ).all()
                    if updates:
                        head = await session.get(CollaborationDocument, draft_id)
                        if head is not None and head.media_ids != last_references:
                            last_references = head.media_ids
                            current = project(
                                decode(head.state),
                                EditorSettings.model_validate(draft.editor_settings),
                            )
                            resolved = await editor_media(
                                session,
                                runtime.settings,
                                current.model_dump_json(),
                                current.settings or EditorSettings(),
                                strict=False,
                            )
                            await send(
                                {
                                    "type": "media",
                                    "media_urls": {
                                        key: value.model_dump() for key, value in resolved.items()
                                    },
                                }
                            )
                for update in updates:
                    await send(
                        {
                            "type": "update",
                            "committed_seq": update.committed_seq,
                            "payload": base64.b64encode(update.payload).decode(),
                            "update_id": str(update.update_id),
                        }
                    )
                    seq = update.committed_seq
                if time.monotonic() - last_checkpoint > 10:
                    last_checkpoint = time.monotonic()
                    from app.modules.content.version_service import auto_checkpoint

                    async with runtime.database.session_factory() as checkpoint_session:
                        await auto_checkpoint(checkpoint_session, draft_id)
                now = time.time()
                if now - last_presence >= 0.35:
                    last_presence = now
                    try:
                        if not await redis_result(
                            runtime.redis.eval(
                                LEASE,
                                2,
                                room,
                                client_key,
                                str(now),
                                str(now + 20),
                                member,
                                str(connection_id),
                            )
                        ):
                            raise AuthFailure(409, 40974, "协同连接已在其他页面恢复")
                        peer.block_id, peer.cursor = presence.block_id, presence.cursor
                        await redis_result(
                            runtime.redis.hset(peers_key, member, peer.model_dump_json())
                        )
                        await runtime.redis.expire(peers_key, 30)
                        active = await runtime.redis.zrangebyscore(room, now, "+inf")
                        values = (
                            await redis_result(runtime.redis.hmget(peers_key, active))
                            if active
                            else []
                        )
                        all_fields = await redis_result(runtime.redis.hkeys(peers_key))
                        stale = set(all_fields) - set(active)
                        if stale:
                            await redis_result(runtime.redis.hdel(peers_key, *stale))
                        await send({"type": "peers", "peers": [json.loads(v) for v in values if v]})
                    except (RedisError, OSError):
                        # No cursor/connection presence is cached in durable content.
                        await send({"type": "peers", "peers": []})
                if len(updates) >= 16:
                    await asyncio.sleep(0)
                else:
                    try:
                        # Notification only wakes durable catch-up; periodic polling covers loss.
                        await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.35)
                    except (RedisError, OSError):
                        await asyncio.sleep(0.35)

        tasks = [asyncio.create_task(incoming()), asyncio.create_task(outgoing())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (WebSocketDisconnect, TimeoutError):
        pass
    except AuthFailure as exc:
        with suppress(Exception):
            await send({"type": "error", "code": exc.code, "msg": exc.message})
    except (ValidationError, ValueError, TypeError, KeyError):
        with suppress(Exception):
            await send({"type": "error", "code": 42270, "msg": "协同消息格式无效，请导出本地内容"})
    except Exception as exc:
        logger.warning("Collaboration transport unavailable: %s", type(exc).__name__)
        with suppress(Exception):
            await send({"type": "error", "code": 50370, "msg": "同步暂不可用，本地输入已保留"})
    finally:
        with suppress(Exception):
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        if member:
            with suppress(RedisError, OSError):
                await runtime.redis.zrem(room, member)
                await redis_result(runtime.redis.hdel(peers_key, member))
                if client_key:
                    await redis_result(
                        runtime.redis.eval(
                            "if redis.call('GET',KEYS[1])==ARGV[1] then "
                            "return redis.call('DEL',KEYS[1]) end return 0",
                            1,
                            client_key,
                            str(connection_id),
                        )
                    )
        with suppress(Exception):
            await socket.close(code=1000)
