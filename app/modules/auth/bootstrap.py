"""Explicit, repeatable database creation, migration and first administrator seeding."""

import argparse
import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url

from app.core.config import PROJECT_ROOT, Settings, load_settings
from app.core.database import Database
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.security import hash_password, normalize_username


class InitializationError(RuntimeError):
    """Only fixed, non-secret operator guidance may be used as this exception's value."""


def validate_admin(username: str, password: str) -> str:
    name = normalize_username(username)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name):
        raise InitializationError("管理员账号须为 1–64 位字母、数字或 ._-，并以字母/数字开头。")
    if not password or len(password) > 1024:
        raise InitializationError("管理员初始密码不能为空，且不能超过 1024 字符。")
    return name


async def ensure_database(settings: Settings, *, create: bool) -> bool:
    url = make_url(settings.database.url.get_secret_value())
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    target_error: Exception | None = None
    try:
        connection = await asyncpg.connect(dsn, timeout=10)
    except (asyncpg.PostgresError, OSError) as exc:
        # Some server/driver combinations close a missing-database startup connection
        # before surfacing SQLSTATE 3D000. Confirm absence in the catalog, never guess.
        target_error = exc
    else:
        await connection.close()
        return False
    database = url.database
    if not database or len(database.encode("utf-8")) > 63:
        raise InitializationError("目标数据库名称无效。")
    maintenance = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    try:
        connection = await asyncpg.connect(maintenance, timeout=10)
    except asyncpg.InsufficientPrivilegeError as exc:
        raise InitializationError(
            "目标库连接失败，且无权连接 postgres 维护库确认目标是否存在；请先创建数据库并授权。"
        ) from exc
    try:
        exists = await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = $1)", database
        )
        if exists:
            assert target_error is not None
            raise target_error
        if not create:
            raise InitializationError("目标数据库不存在；请先创建，或不传 --no-create-database。")
        quoted = '"' + database.replace('"', '""') + '"'
        try:
            await connection.execute("CREATE DATABASE " + quoted)
        except asyncpg.InsufficientPrivilegeError as exc:
            raise InitializationError(
                "目标数据库不存在，当前数据库角色缺少 CREATEDB 权限；请先创建目标库并授权后重试。"
            ) from exc
        except asyncpg.DuplicateDatabaseError:
            return False
        return True
    finally:
        await connection.close()


async def seed_admin(settings: Settings, username: str, password: str) -> bool:
    name = validate_admin(username, password)
    encoded = hash_password(password)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            now = datetime.now(UTC)
            user_id = await session.scalar(
                insert(User)
                .values(
                    id=uuid4(),
                    username=name,
                    display_name=name,
                    password_hash=encoded,
                    role="admin",
                    active=True,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[User.username])
                .returning(User.id)
            )
            if user_id is not None:
                session.add(
                    AuthAuditEvent(user_id=user_id, action="admin_initialized", created_at=now)
                )
            await session.commit()
            return user_id is not None
    finally:
        await database.close()


def initialize(
    settings: Settings,
    *,
    username: str = "admin",
    password: str = "123456",
    create_database: bool = True,
) -> tuple[bool, bool]:
    """No drops, resets, startup hooks or WordPress imports; repeated calls preserve data."""
    validate_admin(username, password)
    created_database = asyncio.run(ensure_database(settings, create=create_database))
    migration = Config(str(PROJECT_ROOT / "migrations" / "alembic.ini"))
    migration.attributes["settings"] = settings
    command.upgrade(migration, "head")
    created_admin = asyncio.run(seed_admin(settings, username, password))
    return created_database, created_admin


def main() -> int:
    parser = argparse.ArgumentParser(
        description="显式初始化 Everunfold 数据库及默认管理员；不会清库或重置已有账号"
    )
    parser.add_argument(
        "--config", type=Path, help="配置文件；默认遵循 CONFIG_FILE/项目根目录 config.yaml"
    )
    parser.add_argument(
        "--admin-username", default=os.environ.get("EVERUNFOLD_ADMIN_USERNAME", "admin")
    )
    parser.add_argument(
        "--password-env", default="EVERUNFOLD_ADMIN_PASSWORD", help="读取初始密码的环境变量名"
    )
    parser.add_argument(
        "--no-create-database", action="store_true", help="数据库不存在时退出，不申请创建"
    )
    args = parser.parse_args()
    password = os.environ.get(args.password_env, "123456")
    try:
        settings = load_settings(args.config)
        created_database, created_admin = initialize(
            settings,
            username=args.admin_username,
            password=password,
            create_database=not args.no_create_database,
        )
    except InitializationError as exc:
        print(f"初始化未完成。{exc}")
        return 1
    except Exception as exc:
        # Database drivers may embed a DSN or SQL parameters; never print their exception values.
        print(
            f"初始化未完成（{type(exc).__name__}）。请检查配置、数据库连接/创建权限及迁移状态；已有数据未被清空。"
        )
        return 1
    print("数据库已创建并迁移。" if created_database else "数据库迁移已检查完成。")
    print("初始管理员已创建。" if created_admin else "同名账号已存在，密码与权限保持原样。")
    return 0
