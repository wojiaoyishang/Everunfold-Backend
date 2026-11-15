"""Validate the actual backend configuration inside its deployment environment."""

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config_probes import backend_checks  # noqa: E402

from app.core.config import load_settings  # noqa: E402


async def main(require_empty: bool) -> int:
    settings = load_settings()
    url = settings.database.url.get_secret_value()
    if require_empty:
        connection = await asyncpg.connect(
            url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=5
        )
        try:
            async with connection.transaction(readonly=True):
                count = await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "AND table_type = 'BASE TABLE'"
                )
            if count:
                print("数据库含有现有表，拒绝作为新库初始化；已有实例请使用迁移。")
                return 1
            print("数据库连接通过，空库检查通过。")
            return 0
        finally:
            await connection.close(timeout=2)
    results = await backend_checks(
        url,
        settings.redis.url.get_secret_value(),
        settings.celery.broker_url.get_secret_value(),
        ROOT,
    )
    for item in results:
        print(f"{item.name}: {item.state} — {item.detail}")
    return 0 if all(item.state == "ok" for item in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-empty", action="store_true")
    try:
        raise SystemExit(asyncio.run(main(parser.parse_args().require_empty)))
    except Exception:
        raise SystemExit("配置或连接检查失败，请返回部署助手检查；详细凭据不会输出。") from None
