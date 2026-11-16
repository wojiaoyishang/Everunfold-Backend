"""Register one deployment binding and export only that frontend's credentials."""

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
from app.modules.settings.node_registry import registry_record  # noqa: E402
from tools.import_frontend_nodes import run as import_nodes  # noqa: E402


async def export() -> str:
    config_path = configuration_path()
    environment = dict(os.environ)
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        document = read_deployment(config_path)
        meta = document["assistant"]
        environment = {**document.get("environment", {}), **environment}
    else:
        data = config_path.parent
        meta = json.loads((data / "setup.json").read_text())
    pairing = setup_frontend_binding(meta, environment)
    if pairing is None:
        raise ValueError("请在助手中填写有效的节点 ID 和内部接收器 URL。")
    node, target = pairing
    inbound_name = target.nextjs_to_fastapi_token_env
    outbound_name = target.fastapi_to_nextjs_token_env
    inbound, outbound = environment[inbound_name], environment[outbound_name]
    config = load_settings()
    database = Database(config.database.url.get_secret_value())
    try:
        async with database.session() as session:
            epoch = await session.scalar(
                text("SELECT source_epoch FROM site_settings WHERE id = 1")
            )
            epoch = str(UUID(str(epoch)))
            needs_seed = await registry_record(session) is None
        if needs_seed:
            await import_nodes(False)
        await import_nodes(True, (node, target))
    finally:
        await database.close()
    document = {
        "format": "everunfold-node-connection/v1",
        "node_id": node,
        "backend_public_url": config.BACKEND_BASE_URL,
        "source_epoch": epoch,
        "tokens": {
            "inbound": {"name": inbound_name, "value": inbound},
            "outbound": {"name": outbound_name, "value": outbound},
        },
    }
    return encode_bundle(document)


if __name__ == "__main__":
    try:
        result = asyncio.run(export())
        print(MARKER + result)
        print("请在 /_nodes 添加或启用该节点的公开地址。")
    except Exception:
        raise SystemExit("无法导出节点连接 Base64，请检查配置、数据库迁移和节点状态。") from None
