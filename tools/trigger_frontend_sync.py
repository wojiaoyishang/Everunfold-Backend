"""Requeue delivery to the configured frontend using deployment database access."""

import argparse
import asyncio
import re

from app.core.config import ConfigurationError, load_settings
from app.core.database import Database
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.backend_plugins import install_backend_plugins
from app.modules.settings.manual_sync import stage_node_sync
from app.modules.settings.node_registry import NODE_ID, read_directory


async def trigger_sync(config_path: str | None, requested_node: str | None) -> str:
    settings = load_settings(config_path)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            await lock_references(session)
            receivers = (await read_directory(session, settings)).receivers
            pairing = settings.setup_frontend
            node_id = requested_node or (pairing[0] if pairing else None)
            if node_id is None and len(receivers) == 1:
                node_id = next(iter(receivers))
            if node_id is None:
                raise SystemExit("请在配置助手选定前端节点，或用 --node-id 指定已启用的节点。")
            if node_id not in receivers:
                raise SystemExit("请先在后端 /_nodes 启用所选节点并绑定内部接收器。")
            await install_backend_plugins(session, settings, commit=False)
            await stage_node_sync(session, settings, node_id, actor="deployment_cli")
            await session.commit()
            return node_id
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="默认读取 CONFIG_FILE 或项目根目录 config.yaml")
    parser.add_argument("--node-id", help="覆盖配置助手保存的默认前端节点 ID")
    args = parser.parse_args()
    if args.node_id is not None and not re.fullmatch(NODE_ID, args.node_id):
        parser.error("前端节点 ID 无效")
    try:
        node_id = asyncio.run(trigger_sync(args.config, args.node_id))
    except ConfigurationError:
        raise SystemExit("配置无法读取，请检查 CONFIG_FILE 和部署配置。") from None
    except AuthFailure as error:
        raise SystemExit(error.message) from None
    except Exception as error:
        raise SystemExit(
            f"触发同步失败（{type(error).__name__}）。请检查数据库连接、迁移和部署日志。"
        ) from None
    print(f"节点 {node_id} 的同步已排队，Worker 投递完成后前端更新。", flush=True)


if __name__ == "__main__":
    main()
