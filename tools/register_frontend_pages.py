"""Install a locally reviewed, built frontend manifest; no remote code loading."""

import argparse
import asyncio
from pathlib import Path

from app.core.config import load_settings
from app.core.database import Database
from app.modules.pages.registration import install_manifest
from app.modules.pages.schemas import PageManifest


async def install(config: str, node: str, manifest: Path) -> None:
    settings = load_settings(config)
    if manifest.stat().st_size > 256 * 1024:
        raise ValueError("oversized frontend manifest")
    value = PageManifest.model_validate_json(manifest.read_bytes())
    db = Database(settings.database.url.get_secret_value())
    try:
        async with db.session_factory() as session:
            await install_manifest(session, settings, node, value)
        print(f"Registered {len(value.pages)} page implementations for the configured node.")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(install(args.config, args.node_id, args.manifest))


if __name__ == "__main__":
    main()
