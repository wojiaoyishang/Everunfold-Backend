"""Owned async engine and per-operation sessions, without automatic DDL."""

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

SCHEMA_REVISION = "0029_comments"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Database:
    def __init__(self, url: str, *, timeout_seconds: int = 2) -> None:
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"timeout": timeout_seconds, "command_timeout": 10},
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def schema_is_current(self) -> bool:
        async with self.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            return bool(revision == SCHEMA_REVISION)

    async def close(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.session_factory()
