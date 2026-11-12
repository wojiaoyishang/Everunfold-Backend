"""Authentication persistence helpers never commit a caller's transaction."""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuthRateLimit, BrowserSession, User
from app.modules.auth.security import token_digest


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def browser_session(
        self, token: str, now: datetime, *, lock: bool = True
    ) -> BrowserSession | None:
        statement = select(BrowserSession).where(
            BrowserSession.token_hash == token_digest(token), BrowserSession.expires_at > now
        )
        return await self.session.scalar(statement.with_for_update() if lock else statement)

    async def user_by_name(self, username: str, *, lock: bool = False) -> User | None:
        statement = (
            select(User).where(User.username == username).execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement.with_for_update() if lock else statement)

    async def user_by_verified_email(self, email: str, *, lock: bool = False) -> User | None:
        statement = (
            select(User)
            .where(
                User.email_verified_at.is_not(None),
                func.lower(func.btrim(User.email)) == email,
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement.with_for_update() if lock else statement)

    async def consume_limit(self, key: str, limit: int, window: int, now: datetime) -> bool:
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        expired = AuthRateLimit.expires_at <= now
        result = await self.session.scalar(
            insert(AuthRateLimit)
            .values(key_hash=key_hash, attempts=1, expires_at=now + timedelta(seconds=window))
            .on_conflict_do_update(
                index_elements=[AuthRateLimit.key_hash],
                set_={
                    "attempts": case((expired, 1), else_=AuthRateLimit.attempts + 1),
                    "expires_at": case(
                        (expired, now + timedelta(seconds=window)), else_=AuthRateLimit.expires_at
                    ),
                },
                where=or_(expired, AuthRateLimit.attempts < limit),
            )
            .returning(AuthRateLimit.attempts)
        )
        return result is not None

    async def collect_expired(self, now: datetime) -> None:
        # Bounded opportunistic cleanup, never a broad table purge.
        for model, key in (
            (BrowserSession, BrowserSession.token_hash),
            (AuthRateLimit, AuthRateLimit.key_hash),
        ):
            expired = select(key).where(model.expires_at <= now).limit(100)
            await self.session.execute(delete(model).where(key.in_(expired)))
