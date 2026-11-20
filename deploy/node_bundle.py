"""Register one deployment binding and export only that frontend's credentials."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from connection_bundle import MARKER, encode_bundle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.config import configuration_path, load_settings, setup_frontend_binding  # noqa: E402
from app.core.database import Database  # noqa: E402
from app.core.deployment_config import read_deployment  # noqa: E402
from app.modules.settings.node_registry import deployed_binding, registry_directory, registry_record  # noqa: E402
from tools.import_frontend_nodes import BindingConflict  # noqa: E402
from tools.import_frontend_nodes import run as import_nodes  # noqa: E402


async def export(node_id: str | None = None, *, preserve_binding: bool = False) -> str:
    config_path = configuration_path()
    environment = dict(os.environ)
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        document = read_deployment(config_path)
        meta = document.get("assistant")
        environment = {**document.get("environment", {}), **environment}
    else:
        meta = (
            json.loads((config_path.parent / "setup.json").read_text()) if node_id is None else None
        )
    requested = setup_frontend_binding(meta, environment) if node_id is None else None
    if node_id is None and requested is None:
        raise ValueError("请在助手中填写有效的节点 ID 和内部接收器 URL。")
    config = load_settings()
    node = requested[0] if requested else node_id
    assert node is not None
    declared = config.frontends.get(node)
    staged = requested[1] if requested else declared
    if (
        preserve_binding
        and declared is not None
        and staged is not None
        and (staged.nextjs_to_fastapi_token_env, staged.fastapi_to_nextjs_token_env)
        != (declared.nextjs_to_fastapi_token_env, declared.fastapi_to_nextjs_token_env)
    ):
        raise BindingConflict
    database = Database(config.database.url.get_secret_value())
    try:
        async with database.session() as session:
            epoch = await session.scalar(
                text("SELECT source_epoch FROM site_settings WHERE id = 1")
            )
            record = await registry_record(session)
            existing = registry_directory(record).bindings.get(node) if record is not None else None
        target = deployed_binding(staged) if staged is not None else None
        if existing is not None:
            if node_id is not None or (
                target is not None
                and (
                    existing.nextjs_to_fastapi_token_env,
                    existing.fastapi_to_nextjs_token_env,
                )
                == (
                    target.nextjs_to_fastapi_token_env,
                    target.fastapi_to_nextjs_token_env,
                )
            ):
                target = existing
            elif preserve_binding:
                raise BindingConflict
        if target is None:
            raise ValueError("请先在部署配置或 /_nodes 登记此节点。")
        result = encode_bundle(
            {
                "format": "everunfold-node-connection/v1",
                "node_id": node,
                "backend_public_url": config.BACKEND_BASE_URL,
                "source_epoch": str(UUID(str(epoch))),
                "tokens": {
                    "inbound": {
                        "name": target.nextjs_to_fastapi_token_env,
                        "value": target.token(config, node, "inbound", environment=environment),
                    },
                    "outbound": {
                        "name": target.fastapi_to_nextjs_token_env,
                        "value": target.token(config, node, "outbound", environment=environment),
                    },
                },
            }
        )
        if target is not existing:
            if record is None:
                await import_nodes(False)
            await import_nodes(True, (node, target), preserve_binding=preserve_binding)
        return result
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node", help="Export a node registered in /_nodes or declared in application.frontends."
    )
    parser.add_argument("--preserve-binding", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(export(args.node, preserve_binding=args.preserve_binding))
        print(MARKER + result)
        print("请在 /_nodes 登记并启用已保存的前端配对。前端发布 URL 由前端自行上报。")
    except BindingConflict:
        raise SystemExit("EVERUNFOLD_NODE_BINDING_CONFLICT") from None
    except Exception:
        raise SystemExit("无法导出节点连接 Base64，请检查配置、数据库迁移和节点状态。") from None
