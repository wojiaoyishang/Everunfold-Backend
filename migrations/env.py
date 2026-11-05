"""Explicit Alembic process; application startup never invokes this module."""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import load_settings
from app.core.database import Base
from app.modules.auth import models as auth_models  # noqa: F401 - include module metadata
from app.modules.content import (
    draft_models,  # noqa: F401
    models,  # noqa: F401 - include module metadata
    publication_models,  # noqa: F401
)
from app.modules.content.collaboration import models as collaboration_models  # noqa: F401
from app.modules.media import models as media_models  # noqa: F401
from app.modules.settings import models as settings_models  # noqa: F401
from app.modules.shortcodes import models as shortcode_models  # noqa: F401

settings = context.config.attributes.get("settings") or load_settings()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database.url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(
        settings.database.url.get_secret_value(), poolclass=pool.NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            # Serialize only explicitly invoked migrations; normal processes never take this lock.
            await connection.exec_driver_sql("SELECT pg_advisory_lock(730104210)")
            await connection.commit()
            try:
                await connection.run_sync(run_with_connection)
            finally:
                await connection.exec_driver_sql("SELECT pg_advisory_unlock(730104210)")
                await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
