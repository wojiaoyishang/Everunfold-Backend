"""Install a locally reviewed, built frontend manifest; no remote code loading."""

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import load_settings
from app.core.database import Database
from app.modules.pages.registration import install_manifest
from app.modules.pages.schemas import PageManifest


async def install(config: str, node: str, manifest: Path) -> None:
    settings = load_settings(config)
    data = sys.stdin.buffer.read(256 * 1024 + 1) if str(manifest) == "-" else manifest.read_bytes()
    if len(data) > 256 * 1024:
        raise ValueError("oversized frontend manifest")
    value = PageManifest.model_validate_json(data)
    db = Database(settings.database.url.get_secret_value())
    try:
        async with db.session_factory() as session:
            await install_manifest(session, settings, node, value)
        print(f"Registered {len(value.pages)} page types for the configured node.")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default=None, help="默认读取 CONFIG_FILE，未设置时使用项目根目录 config.yaml"
    )
    parser.add_argument("--node-id", required=True)
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Reviewed JSON file, or - for stdin"
    )
    args = parser.parse_args()
    asyncio.run(install(args.config, args.node_id, args.manifest))


if __name__ == "__main__":
    main()
