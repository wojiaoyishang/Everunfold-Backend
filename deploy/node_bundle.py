"""Register one deployment binding and export only that frontend's credentials."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from config_files import Files
from config_validation import identifier, public_url, token

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.config import FrontendConfig, load_settings  # noqa: E402
from app.core.database import Database  # noqa: E402
from app.core.deployment_config import read_deployment  # noqa: E402
from app.modules.settings.node_registry import registry_record  # noqa: E402
from tools.import_frontend_nodes import run as import_nodes  # noqa: E402


async def export() -> Path:
    config_path = Path(os.environ.get("CONFIG_FILE", "config.toml")).resolve()
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        meta = read_deployment(config_path)["assistant"]
        data = Path(os.environ.get("EVERUNFOLD_DATA_DIR", str(ROOT / "var/instance")))
    else:
        data = config_path.parent
        meta = json.loads((data / "setup.json").read_text())
    node, receiver = meta["node"], meta["receiver"]
    if not identifier(node) or not public_url(receiver):
        raise ValueError("请在助手中填写有效的节点 ID 和内部接收器 URL。")
    inbound_name, outbound_name = meta["inbound_env"], meta["outbound_env"]
    inbound, outbound = os.environ.get(inbound_name, ""), os.environ.get(outbound_name, "")
    if not token(inbound) or not token(outbound) or inbound == outbound:
        raise ValueError("节点令牌缺失或无效。")
    config = load_settings()
    target = FrontendConfig(
        base_url=receiver,
        settings_receiver_url=receiver,
        nextjs_to_fastapi_token_env=inbound_name,
        fastapi_to_nextjs_token_env=outbound_name,
    )
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
    path = data / "connections" / (node + ".json")
    files = Files()
    files.read(path)
    files.write({path: json.dumps(document, indent=2) + "\n"})
    return path


if __name__ == "__main__":
    try:
        result = asyncio.run(export())
        print(f"已导出节点连接文件：{result}")
        print("请在 /_nodes 添加或启用该节点的公开地址。")
    except Exception:
        raise SystemExit("无法导出连接文件，请检查配置、数据库迁移和节点状态。") from None
