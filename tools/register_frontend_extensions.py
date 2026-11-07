"""Register locally compiled plugin/theme descriptors without accepting executable code."""

import argparse
import asyncio
import sys

from app.core.config import load_settings
from app.core.database import Database
from app.modules.settings.extensions_schemas import ExtensionInventory
from app.modules.settings.extensions_service import install_inventory


async def install(config: str, node: str) -> None:
    encoded = sys.stdin.buffer.read(512 * 1024 + 1)
    if len(encoded) > 512 * 1024:
        raise ValueError("oversized extension manifest")
    value = ExtensionInventory.model_validate_json(encoded)
    settings = load_settings(config)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            await install_inventory(session, settings, node, value)
    finally:
        await database.close()
    print(f"Registered {len(value.plugins)} plugins and {len(value.themes)} themes.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--node-id", required=True)
    args = parser.parse_args()
    asyncio.run(install(args.config, args.node_id))


if __name__ == "__main__":
    main()
