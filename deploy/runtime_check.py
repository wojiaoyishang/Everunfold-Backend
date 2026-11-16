"""Check dependencies; optionally initialize an empty database before deployment."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config_probes import backend_checks, database_is_empty, failure  # noqa: E402
from database_setup import initialization_idle, initialize_empty  # noqa: E402

from app.core.config import configuration_path, load_settings  # noqa: E402
from app.core.deployment_config import read_deployment  # noqa: E402


def deployment_environment() -> tuple[Path, dict[str, str]]:
    config = configuration_path(project_root=ROOT)
    environment = dict(os.environ)
    if config.suffix.lower() in {".yaml", ".yml"}:
        environment = {**read_deployment(config).get("environment", {}), **environment}
    environment["CONFIG_FILE"] = str(config)
    return config, environment


async def main(require_empty: bool = False, prepare: bool = False, initialize: bool = False) -> int:
    config, environment = deployment_environment()
    settings = load_settings(config, environ=environment)
    url = settings.database.url.get_secret_value()
    if require_empty:
        connection = await asyncpg.connect(
            url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=5
        )
        try:
            async with connection.transaction(readonly=True):
                empty = await database_is_empty(connection)
            print("空库检查通过。" if empty else "数据库已有对象，拒绝按空库初始化。", flush=True)
            return 0 if empty else 1
        finally:
            await connection.close(timeout=2)

    async def check():
        results = await backend_checks(
            url,
            settings.redis.url.get_secret_value(),
            settings.celery.broker_url.get_secret_value(),
            ROOT,
        )
        for item in results:
            print(f"{item.name}: {item.state} — {item.detail}", flush=True)
        return results

    results = await check()
    if all(item.state == "ok" for item in results):
        if (prepare or initialize) and not await initialization_idle(settings):
            return 1
        if initialize:
            print("数据库已初始化，跳过初始化，保留原账号和数据。", flush=True)
        return 0
    if any(item.state == "error" for item in results):
        print("部署检查未通过，未启动服务；请修正上方错误后重试。", flush=True)
        return 1
    empty = any(item.code == "empty" for item in results)
    if not empty:
        print(
            "已有数据库需要升级。请先备份，再运行 ./setup.sh --docker migrate（原生用 --native）。",
            flush=True,
        )
        return 1
    if not (prepare or initialize):
        print("这是只读检查；启用“空库自动初始化”后执行 start，或单独执行 init-db。", flush=True)
        return 1
    enabled = environment.get("EVERUNFOLD_AUTO_INIT_EMPTY_DB", "1")
    if prepare and enabled not in ("0", "1"):
        print("EVERUNFOLD_AUTO_INIT_EMPTY_DB 须为 1（开启）或 0（关闭）。", flush=True)
        return 1
    if prepare and enabled == "0":
        print("空库自动初始化已关闭，未启动服务；请执行 init-db 或在助手中开启此项。", flush=True)
        return 1
    # This dedicated deployment process also runs the existing plugin registrar.
    os.environ.update(environment)
    if not await initialize_empty(settings, config, environment, ROOT):
        return 1
    results = await check()
    if not all(item.state == "ok" for item in results):
        print("初始化后检查未通过，未启动服务；请根据上方结果处理后重试。", flush=True)
        return 1
    print("数据库初始化及检查通过。", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-empty", action="store_true")
    mode.add_argument("--prepare", action="store_true", help="按配置初始化空库，然后检查")
    mode.add_argument("--initialize", action="store_true", help="显式初始化空库，然后检查")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args.require_empty, args.prepare, args.initialize)))
    except KeyboardInterrupt:
        raise SystemExit("部署检查已中断，未启动服务。") from None
    except Exception as error:
        raise SystemExit(
            f"部署准备失败（{type(error).__name__}）：{failure(error)}；未启动服务。"
        ) from None
