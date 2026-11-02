"""Replica-safe socket transport. Database catch-up does not depend on Redis delivery."""

import asyncio
import base64
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
from app.modules.content.collaboration.models import CollaborationDocument, CollaborationUpdate
from app.modules.content.collaboration.schemas import ClientUpdate, Peer, PresenceInput
from app.modules.content.collaboration.service import (
    accept_update,
    authorize,
    consume_ticket,
    draft_row,
)

logger = logging.getLogger(__name__)
COLORS = ("#4869b0", "#397d66", "#a65376", "#9c6b33", "#7a57b1")

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
    if runtime is None or socket.headers.get("origin") not in runtime.settings.cors.allowed_origins:
        await socket.close(code=1008)
        return
    await socket.accept()
    connection_id = uuid4()
    room = redis_key(runtime.settings, "collab", str(draft_id), "leases")
    peers_key = redis_key(runtime.settings, "collab", str(draft_id), "peers")
    channel = redis_channel(runtime.settings, "collab", str(draft_id), "updates")
    member = ""
    client_key = ""
    send_lock = asyncio.Lock()

    async def send(value: dict[str, Any]) -> None:
        async with send_lock, asyncio.timeout(5):
            await socket.send_json(value)

    try:
        async with asyncio.timeout(8):
            auth = await socket.receive_json()
        if (
            not isinstance(auth, dict)
            or set(auth) != {"type", "ticket", "client_id"}
            or auth["type"] != "auth"
            or not isinstance(auth["ticket"], str)
            or len(auth["ticket"]) > 128
            or type(auth["client_id"]) is not int
            or not 0 <= auth["client_id"] <= 4_294_967_295
        ):
            raise AuthFailure(401, 40102, "协同连接验证失败")
        client_id = auth["client_id"]
        client_key = redis_key(runtime.settings, "collab", str(draft_id), "client", str(client_id))
        async with runtime.database.session_factory() as session:
            session_hash, actor = await consume_ticket(session, draft_id, auth["ticket"])
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
            color=COLORS[actor.id.int % len(COLORS)],
        )
        presence = PresenceInput()
        async with runtime.database.session_factory() as session:
            document = await session.get(CollaborationDocument, draft_id)
            draft = await draft_row(session, draft_id)
            assert document is not None
            seq, revision = document.committed_seq, draft.revision
            await send(
                {
                    "type": "sync",
                    "epoch": str(document.epoch),
                    "committed_seq": seq,
                    "payload": base64.b64encode(document.state).decode(),
                    "revision": revision,
                }
            )

        async def incoming() -> None:
            nonlocal presence
            while True:
                raw = await socket.receive_text()
                if len(raw) > 1_500_000:
                    raise AuthFailure(413, 41370, "协同更新过大，请导出本地内容")
                value = json.loads(raw)
                if value.get("type") == "presence":
                    presence = PresenceInput.model_validate(value)
                    continue
                update = ClientUpdate.model_validate(value)
                async with runtime.database.session_factory() as session:
                    committed = await accept_update(
                        session, runtime.settings, draft_id, session_hash, update
                    )
                # This point is reachable only after PostgreSQL commit; duplicates re-ACK same seq.
                await send(
                    {"type": "ack", "update_id": str(update.update_id), "committed_seq": committed}
                )
                with suppress(RedisError, OSError):
                    await runtime.redis.publish(channel, str(committed))

        async def outgoing() -> None:
            nonlocal seq, revision
            last_presence = 0.0
            while True:
                async with runtime.database.session_factory() as session:
                    await authorize(session, session_hash)
                    draft = await draft_row(session, draft_id)
                    if draft.revision != revision:
                        revision = draft.revision
                        await send({"type": "revision", "revision": revision})
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
                now = time.time()
                if now - last_presence >= 2:
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
                await asyncio.sleep(0.35 if len(updates) < 16 else 0)

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
