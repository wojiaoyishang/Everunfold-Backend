"""Start local settings delivery helpers; API and Next.js remain separate processes."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import PROJECT_ROOT, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-root", type=Path, required=True)
    args = parser.parse_args()
    frontend = args.frontend_root.resolve()
    if not (frontend / "tools/settings-receiver.mts").is_file():
        raise SystemExit("The matching frontend receiver was not found")
    settings = load_settings()
    active = [
        node for node in settings.frontends.values() if node.enabled and node.settings_receiver_url
    ]
    if len(active) != 1:
        raise SystemExit("Local helper requires exactly one enabled settings receiver")
    secret_file = PROJECT_ROOT / "var" / "settings-dev-secrets.json"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        values = json.loads(secret_file.read_text(encoding="utf-8"))
    else:
        values = {active[0].fastapi_to_nextjs_token_env: secrets.token_urlsafe(48)}
        # Runtime-only local development credentials, never printed or committed.
        with secret_file.open("x", encoding="utf-8") as handle:
            json.dump(values, handle)
        secret_file.chmod(0o600)
    if not isinstance(values, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in values.items()
    ):
        raise SystemExit("Invalid local development credential file")
    env = {**os.environ, **values, "PYTHONIOENCODING": "utf-8"}
    node_env = {**env, "EVERUNFOLD_PROJECT_ROOT": str(frontend)}
    node_env.pop("CONFIG_FILE", None)
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is unavailable")
    commands = [
        ([node, "--import", "tsx", "tools/settings-receiver.mts"], frontend, node_env),
        (
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.entrypoints.worker:celery_app",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=WARNING",
                "--without-mingle",
                "--without-gossip",
            ],
            PROJECT_ROOT,
            env,
        ),
        ([sys.executable, "-m", "app.entrypoints.scheduler"], PROJECT_ROOT, env),
    ]
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for command, cwd, environment in commands:
            processes.append(subprocess.Popen(command, cwd=cwd, env=environment))
        print(
            "Settings receiver, Worker and Scheduler started. Ctrl+C stops these helpers.",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        raise RuntimeError("A settings helper stopped; inspect its diagnostic output")
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
