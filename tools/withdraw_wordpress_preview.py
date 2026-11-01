"""Opt-in local development operation. Dry-run first; never touches a WordPress backup."""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.models import User
from app.modules.content.imported_preview import withdraw_imported_preview
from app.modules.content.models import Content
from app.modules.content.repository import public_conditions


async def run(config: Path | None, *, apply: bool, username: str) -> dict[str, int | None]:
    settings = load_settings(config)
    url = make_url(settings.database.url.get_secret_value())
    database_name = url.database or ""
    if url.host not in {"127.0.0.1", "localhost", "::1"} or not (
        database_name == "everunfold_dev"
        or (
            database_name.startswith("everunfold_")
            and "test" in database_name
            and settings.redis.namespace_scope.startswith("test")
        )
    ):
        raise ValueError(
            "This operation only accepts the local development or explicit test database"
        )
    database = Database(settings.database.url.get_secret_value())
    try:
        if not await database.schema_is_current():
            raise ValueError("Apply explicit migrations first")
        async with database.session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Content)
                .where(Content.source == "wordpress", *public_conditions())
            )
            if not apply:
                return {"eligible": count or 0}
            actor = await session.scalar(
                select(User).where(
                    User.username == username, User.role == "admin", User.active.is_(True)
                )
            )
            if actor is None:
                raise ValueError("An existing active administrator is required for the audit")
            changed, revision = await withdraw_imported_preview(session, settings, actor.id)
            return {"withdrawn": changed, "revision": revision}
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--apply", action="store_true", help="实际撤回；默认只预检")
    parser.add_argument("--username", default="admin", help="审计所属的现有管理员")
    args = parser.parse_args()
    result = asyncio.run(run(args.config, apply=args.apply, username=args.username))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
