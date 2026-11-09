"""Inspect or explicitly apply locally installed Python plugins; never runs migrations."""

import argparse
import asyncio

from app.core.config import load_settings
from app.core.database import Database
from app.core.plugins.loader import plugin_catalog
from app.modules.settings.backend_plugins import install_backend_plugins


async def apply(config: str) -> None:
    settings = load_settings(config)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            changed = await install_backend_plugins(session, settings)
    finally:
        await database.close()
    print(
        "Backend plugin catalog applied; delivery queued."
        if changed
        else "Backend plugin catalog unchanged."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument(
        "--apply", action="store_true", help="Persist declarations and enqueue public settings"
    )
    args = parser.parse_args()
    catalog = plugin_catalog()
    for plugin in catalog.plugins:
        print(
            f"{plugin.info.id} {plugin.info.version}: "
            f"{len(plugin.shortcodes)} shortcodes, {len(plugin.captcha)} CAPTCHA providers"
        )
    if args.apply:
        asyncio.run(apply(args.config))


if __name__ == "__main__":
    main()
