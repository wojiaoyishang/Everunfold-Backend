"""Single scheduler: enqueue ticks only. Business and HTTP work belong to Workers."""

import time
import uuid

from redis import Redis

from app.core.task_queue import celery_app, settings


def main() -> None:
    token = uuid.uuid4().hex
    key = settings.redis_prefix + "scheduler:settings"
    interval = max(1, settings.outbox.poll_interval_seconds)
    lease_ms = max(15000, interval * 5000)
    client = Redis.from_url(
        settings.redis.url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
    )
    try:
        if not client.set(key, token, nx=True, px=lease_ms):
            raise RuntimeError("A settings scheduler already owns this namespace")
        while True:
            renewed = client.eval(
                "if redis.call('get',KEYS[1]) == ARGV[1] then "
                "return redis.call('pexpire',KEYS[1],ARGV[2]) else return 0 end",
                1,
                key,
                token,
                str(lease_ms),
            )
            if not renewed:
                raise RuntimeError("Scheduler lease was lost")
            celery_app.send_task("everunfold.settings.deliver", expires=interval * 2)
            celery_app.send_task("everunfold.auth.deliver_callbacks", expires=interval * 2)
            celery_app.send_task("everunfold.content.publish_due", expires=interval * 2)
            time.sleep(interval)
    finally:
        client.eval(
            "if redis.call('get',KEYS[1]) == ARGV[1] then "
            "return redis.call('del',KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
        client.close()


if __name__ == "__main__":
    main()
