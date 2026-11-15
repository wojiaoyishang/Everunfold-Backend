"""Initialize only a verified empty database, before starting backend services."""

import asyncio
import subprocess
import sys
from pathlib import Path

import asyncpg
from config_probes import database_state

from app.core.config import Settings

# Session lock scoped to this database; released only after initialization finishes.
INITIALIZE_LOCK = int.from_bytes(b"EVINIT01", "big")


async def initialization_idle(settings: Settings) -> bool:
    # A concurrent initializer may have stamped the schema but still be seeding
    # the administrator or registering plugins. Do not start services yet.
    connection = await asyncpg.connect(
        settings.database.url.get_secret_value().replace(
            "postgresql+asyncpg://", "postgresql://", 1
        ),
        timeout=5,
        command_timeout=5,
    )
    try:
        idle = bool(await connection.fetchval("SELECT pg_try_advisory_lock($1)", INITIALIZE_LOCK))
        if not idle:
            print("另一个部署仍在完成数据库初始化，本次不启动服务；请稍后重试。", flush=True)
        return idle
    finally:
        await connection.close(timeout=2)


async def initialize_empty(
    settings: Settings, config: Path, environment: dict[str, str], root: Path
) -> bool:
    connection = await asyncpg.connect(
        settings.database.url.get_secret_value().replace(
            "postgresql+asyncpg://", "postgresql://", 1
        ),
        timeout=5,
        command_timeout=5,
    )
    try:
        if not await connection.fetchval("SELECT pg_try_advisory_lock($1)", INITIALIZE_LOCK):
            print("另一个部署正在初始化此数据库，本次停止；待其完成后重新启动。", flush=True)
            return False
        # Recheck after acquiring the lock: another deploy may have just completed.
        state = await database_state(connection, root)
        if state.code == "current":
            print("数据库已初始化，跳过初始化，保留原账号和数据。", flush=True)
            return True
        if state.code != "empty":
            print("数据库不是空库，停止初始化。请核对目标库或显式执行升级迁移。", flush=True)
            return False
        password = environment.get("EVERUNFOLD_ADMIN_PASSWORD", "")
        if not 12 <= len(password) <= 1024:
            print(
                "初始化前请在助手填写初始管理员密码（至少 12 位）；不会使用默认弱密码。", flush=True
            )
            return False
        print("[初始化 1/3] 已确认空库，执行核心迁移并创建初始管理员……", flush=True)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(root / "init.database.py"),
            "--config",
            str(config),
            "--no-create-database",
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        try:
            status = await process.wait()
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if status:
            print(
                f"初始化失败（退出码 {status}），不启动服务；请检查上方迁移或权限信息。", flush=True
            )
            return False
        print("[初始化 2/3] 登记后端插件……", flush=True)
        from tools.register_backend_plugins import apply

        await apply(str(config))
        print("[初始化 3/3] 初始化完成，重新检查数据库、Redis 和迁移状态……", flush=True)
        return True
    finally:
        await connection.close(timeout=2)
