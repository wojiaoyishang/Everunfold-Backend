"""Process-wide coordination without keeping a transaction open during storage I/O."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


def lock_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


async def lock_references(session: AsyncSession) -> None:
    # All writers that add media references must take this transaction lock first.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key("media-references")}
    )


@asynccontextmanager
async def media_operation(session: AsyncSession, *identities: str) -> AsyncIterator[AsyncSession]:
    # Release the caller's read transaction. A single pinned connection then owns
    # both the advisory locks and the work session, including explicit commits.
    await session.commit()
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    async with (
        engine.connect() as connection,
        AsyncSession(bind=connection, expire_on_commit=False) as work,
    ):
        acquired: list[int] = []
        try:
            for identity in identities:
                key = lock_key(identity)
                while True:
                    obtained = await work.scalar(
                        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    )
                    if obtained:
                        acquired.append(key)
                    await work.commit()
                    if obtained:
                        break
                    await asyncio.sleep(0.05)
            yield work
        finally:

            async def release() -> None:
                await work.rollback()
                for key in reversed(acquired):
                    await work.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                await work.commit()

            # Never return a connection with session-level locks to the pool.
            cleanup = asyncio.create_task(release())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
