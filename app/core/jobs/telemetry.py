"""Bounded instance snapshots; expired heartbeats remain visibly stale."""

import json
import os
import socket
import threading
import time
import uuid
from contextlib import suppress
from typing import Any

from redis import Redis

from app.core.config import Settings


class Reporter:
    def __init__(self, config: Settings, role: str) -> None:
        self.client = Redis.from_url(
            config.redis.url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
        )
        self.key = config.redis_prefix + "runtime:instance:" + uuid.uuid4().hex
        self.metrics_key = self.key.replace("runtime:instance:", "runtime:metrics:", 1)
        self.data: dict[str, Any] = {
            "id": self.key.rsplit(":", 1)[-1],
            "role": role,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": time.time(),
            "heartbeat": 0,
            "state": "starting",
        }
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def pulse(self, **fields: Any) -> None:
        self.data.update(fields, heartbeat=time.time())
        with suppress(Exception):
            self.client.set(self.key, json.dumps(self.data), ex=86400)

    def background(self) -> None:
        def loop() -> None:
            while not self.stop.wait(5):
                self.pulse(state="online")

        self.pulse(state="online")
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def sample(self, **fields: Any) -> None:
        """Keep Prometheus-only state out of the management API schema."""
        with suppress(Exception):
            self.client.set(self.metrics_key, json.dumps(fields), ex=120)

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.pulse(state="stopped")
        self.client.close()
