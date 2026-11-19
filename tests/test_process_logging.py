"""Configured levels must reach the real process entry points and stderr."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.process_logging import RuntimeLogFormatter

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("variable", ["API_LOG_LEVEL", "SCHEDULER_LOG_LEVEL"])
def test_process_levels_filter_stderr_and_reject_invalid_values(variable: str) -> None:
    source = (
        "import logging; from app.core.process_logging import configure_process_logging; "
        f"configure_process_logging('test', {variable!r}); "
        "logging.getLogger('app.test').info('routine-message'); "
        "logging.getLogger('app.test').warning('failure-message')"
    )
    for level in ("warning", "DEBUG", "invalid"):
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=ROOT,
            env={**os.environ, variable: level},
            capture_output=True,
            text=True,
            timeout=10,
        )
        if level == "invalid":
            assert result.returncode != 0
            assert variable + " must be" in result.stderr
        else:
            assert result.returncode == 0
            assert "failure-message" in result.stderr
            assert ("routine-message" in result.stderr) == (level == "DEBUG")


def test_docker_entrypoint_passes_independent_api_and_worker_levels(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (workspace / name).write_text("isolated dependency check")
    entry = (ROOT / "docker/entrypoint.sh").read_text()
    entry = entry.replace("/workspace", str(workspace)).replace("/opt/everunfold", str(workspace))
    script = tmp_path / "entrypoint.sh"
    script.write_text(entry)
    for name in ("python", "celery"):
        program = tmp_path / name
        program.write_text(
            f"#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        )
        program.chmod(0o700)
    for mode, expected in (
        ("api", ["--log-level", "error"]),
        ("worker", ["--loglevel", "WARNING"]),
    ):
        result = subprocess.run(
            ["bash", str(script), mode],
            env={
                **os.environ,
                "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
                "EVERUNFOLD_CONFIG_LOADED": "1",
                "API_LOG_LEVEL": "ERROR",
                "WORKER_LOG_LEVEL": "WARNING",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        arguments = json.loads(result.stdout.splitlines()[-1])
        index = arguments.index(expected[0])
        assert arguments[index : index + 2] == expected


def test_celery_success_keeps_timing_without_task_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVERUNFOLD_LOG_FORMAT", raising=False)
    context = {
        "id": "sample-id",
        "name": "test.publish",
        "runtime": 0.0125,
        "args": "private-task-arguments",
        "return_value": "private-task-result",
    }
    record = logging.getLogger("celery.app.trace").makeRecord(
        "celery.app.trace",
        logging.INFO,
        __file__,
        1,
        "Task %(name)s succeeded: %(return_value)s",
        (context,),
        None,
        extra={"data": context, "symbol": "文章.publish"},
    )
    line = RuntimeLogFormatter("celery").format(record)
    assert "private-task-" not in line
    event = json.loads(line)
    assert event["event"] == "task_finished"
    assert event["task_id"] == "sample-id"
    assert event["task_state"] == "SUCCESS"
    assert event["duration_ms"] == 12.5
    assert event["symbol"] == "文章.publish"


@pytest.mark.parametrize(
    ("target", "path", "message"),
    [
        (
            "/v1/account/profile?token=private-query#private-fragment",
            "/v1/account/profile",
            "GET /v1/account/profile → 200",
        ),
        ("http://user:private-password@example.test/profile", None, "GET → 200"),
    ],
)
def test_uvicorn_access_uses_safe_request_fields(monkeypatch, target, path, message) -> None:
    monkeypatch.delenv("EVERUNFOLD_LOG_FORMAT", raising=False)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", "GET", target, "1.1", 200),
        None,
    )
    line = RuntimeLogFormatter().format(record)
    event = json.loads(line)
    assert event["event"] == "http_access"
    assert event["request_url"] == path
    assert event["message"] == message
    assert (event["method"], event["http_version"], event["status"]) == ("GET", "1.1", 200)
    assert "private-" not in line
