"""Real worker pool test. Pass EVERUNFOLD_TEST_REDIS to run with an isolated Redis."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from celery import Celery  # type: ignore[import-untyped]
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from deploy.worker_capacity import capacity_arguments


@pytest.mark.parametrize("bound", ["2,3", "4,0", "2", "4,1,1", "129,1"])
def test_invalid_celery_autoscale_bounds(bound: str) -> None:
    with pytest.raises(ValueError, match="WORKER_AUTOSCALE"):
        capacity_arguments({"WORKER_POOL": "prefork", "WORKER_AUTOSCALE": bound})


def test_celery_autoscale_requires_parallel_pool() -> None:
    with pytest.raises(ValueError, match="prefork"):
        capacity_arguments({"WORKER_AUTOSCALE": "4,1"})
    assert capacity_arguments({"WORKER_POOL": "prefork", "WORKER_CONCURRENCY": "8"}) == [
        "--pool",
        "prefork",
        "--concurrency",
        "8",
    ]


def test_prefork_expands_for_tasks_and_releases_idle_processes(tmp_path: Path) -> None:
    binary = os.environ.get("EVERUNFOLD_TEST_REDIS")
    if not binary:
        pytest.skip("set EVERUNFOLD_TEST_REDIS to an isolated redis-server binary")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    broker = f"redis://127.0.0.1:{port}/0"
    environment = dict(os.environ, TEST_BROKER=broker, AUTOSCALE_KEEPALIVE="2")
    (tmp_path / "scale_fixture.py").write_text(
        """import os
import time
from celery import Celery
from redis import Redis

broker = os.environ["TEST_BROKER"]
app = Celery("scale-test", broker=broker)
app.conf.worker_prefetch_multiplier = 1

@app.task(name="scale-fixture.hold", ignore_result=True)
def hold():
    client = Redis.from_url(broker)
    client.sadd("task-pids", os.getpid())
    until = time.monotonic() + 15
    while not client.exists("release") and time.monotonic() < until:
        time.sleep(0.05)
    client.incr("completed")
""",
        encoding="utf-8",
    )
    client = Redis.from_url(broker, socket_timeout=1, socket_connect_timeout=1)
    redis = subprocess.Popen(
        [binary, "--bind", "127.0.0.1", "--port", str(port), "--save", "", "--appendonly", "no"],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
    )
    worker = None
    try:
        for _ in range(100):
            try:
                client.ping()
                break
            except RedisConnectionError:
                assert redis.poll() is None
                time.sleep(0.05)
        else:
            pytest.fail("fixture Redis did not start")
        capacity = capacity_arguments({"WORKER_POOL": "prefork", "WORKER_AUTOSCALE": "4,1"})
        with (tmp_path / "worker.log").open("w") as log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "scale_fixture",
                    "worker",
                    *capacity,
                    "-n",
                    "scale-test@localhost",
                    "--without-mingle",
                    "--without-gossip",
                ],
                cwd=tmp_path,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        producer = Celery(broker=broker)
        inspect = producer.control.inspect(destination=["scale-test@localhost"], timeout=0.5)

        def pool_size() -> int:
            info = (inspect.stats() or {}).get("scale-test@localhost")
            return len(info["pool"]["processes"]) if info else 0

        def wait_for_size(size: int) -> None:
            deadline = time.monotonic() + 14
            while time.monotonic() < deadline:
                if pool_size() == size:
                    return
                time.sleep(0.1)
            pytest.fail(
                f"worker did not reach {size} children:\n{(tmp_path / 'worker.log').read_text()}"
            )

        wait_for_size(1)
        for _ in range(8):
            producer.send_task("scale-fixture.hold")
        wait_for_size(4)
        client.set("release", "yes")
        wait_for_size(1)
        assert client.get("completed") == b"8"
        task_processes = client.scard("task-pids")
        assert isinstance(task_processes, int) and task_processes >= 2
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        client.close()
        redis.terminate()
        redis.wait(timeout=6)
