"""Initialization belongs to deployment preparation, with a real empty-database gate."""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import load_settings
from app.core.deployment_config import read_deployment
from tests.test_deployment_config import write_root

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import database_setup  # noqa: E402
import runtime_check  # noqa: E402
from config_model import Check  # noqa: E402


@pytest.mark.parametrize(
    ("mode", "flag", "state", "redis_ok", "success", "called", "result"),
    [
        ("prepare", None, "empty", True, True, True, 0),
        ("prepare", "0", "empty", True, True, False, 1),
        ("prepare", "invalid", "empty", True, True, False, 1),
        ("check", "1", "empty", True, True, False, 1),
        ("initialize", "0", "empty", True, True, True, 0),
        ("prepare", "1", "current", True, True, False, 0),
        ("initialize", "1", "current", True, True, False, 0),
        ("prepare", "1", "busy", True, True, False, 1),
        ("initialize", "1", "busy", True, True, False, 1),
        ("prepare", "1", "unmanaged", True, True, False, 1),
        ("prepare", "1", "migration", True, True, False, 1),
        ("prepare", "1", "empty", False, True, False, 1),
        ("prepare", "1", "empty", True, False, True, 1),
    ],
)
def test_start_only_initializes_an_empty_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    flag: str | None,
    state: str,
    redis_ok: bool,
    success: bool,
    called: bool,
    result: int,
) -> None:
    config = write_root(tmp_path)
    environment = {**os.environ, **read_deployment(config)["environment"]}
    environment.pop("EVERUNFOLD_AUTO_INIT_EMPTY_DB", None)
    if flag is not None:
        environment["EVERUNFOLD_AUTO_INIT_EMPTY_DB"] = flag
    monkeypatch.setattr(runtime_check, "deployment_environment", lambda: (config, environment))
    idle = state != "busy"
    if not idle:
        state = "current"
    monkeypatch.setattr(runtime_check, "initialization_idle", AsyncMock(return_value=idle))
    before = [
        Check("PostgreSQL", "ok", "connected"),
        Check(
            "数据库迁移",
            "ok" if state == "current" else "error" if state == "unmanaged" else "warning",
            state,
            state,
        ),
        Check("Redis", "ok" if redis_ok else "error", "ping"),
    ]
    checks = AsyncMock(side_effect=[before, [Check("全部", "ok", "ready")]])
    initialize = AsyncMock(return_value=success)
    monkeypatch.setattr(runtime_check, "backend_checks", checks)
    monkeypatch.setattr(runtime_check, "initialize_empty", initialize)
    with patch.dict(os.environ):
        actual = asyncio.run(
            runtime_check.main(prepare=mode == "prepare", initialize=mode == "initialize")
        )
    assert actual == result
    assert initialize.await_count == int(called)
    assert checks.await_count == (2 if called and success else 1)


@pytest.mark.parametrize(
    ("locked", "state", "password", "exit_code", "runs", "expected"),
    [
        (False, "empty", "initial-test-password", 0, False, False),
        (True, "current", "initial-test-password", 0, False, True),
        (True, "unmanaged", "initial-test-password", 0, False, False),
        (True, "migration", "initial-test-password", 0, False, False),
        (True, "empty", "", 0, False, False),
        (True, "empty", "short", 0, False, False),
        (True, "empty", "initial-test-password", 1, True, False),
        (True, "empty", "initial-test-password", 0, True, True),
    ],
)
def test_empty_database_is_rechecked_under_lock_and_failures_stop_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    locked: bool,
    state: str,
    password: str,
    exit_code: int,
    runs: bool,
    expected: bool,
) -> None:
    config = write_root(tmp_path)
    settings = load_settings(config, environ={})
    connection = AsyncMock()
    connection.fetchval.return_value = locked
    monkeypatch.setattr(database_setup.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(
        database_setup, "database_state", AsyncMock(return_value=Check("db", "ok", "", state))
    )
    process = AsyncMock()
    process.wait.return_value = exit_code
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(database_setup.asyncio, "create_subprocess_exec", create)
    apply = AsyncMock()
    monkeypatch.setattr("tools.register_backend_plugins.apply", apply)
    assert (
        asyncio.run(
            database_setup.initialize_empty(
                settings, config, {"EVERUNFOLD_ADMIN_PASSWORD": password}, tmp_path
            )
        )
        == expected
    )
    assert create.await_count == int(runs)
    assert apply.await_count == int(runs and exit_code == 0)
    connection.close.assert_awaited_once()
    if runs:
        args = create.call_args.args
        assert "--no-create-database" in args
        assert password not in args
