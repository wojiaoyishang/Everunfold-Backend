"""Explicitly seed the managed directory or update deployment-only bindings."""

import argparse
import asyncio
from datetime import UTC, datetime

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.models import AuthAuditEvent
from app.modules.media.locking import lock_references
from app.modules.settings.models import FrontendNodeRegistry
from app.modules.settings.node_registry import (
    MAX_NODES,
    deployment_directory,
    registry_directory,
    registry_record,
)
from app.modules.settings.service import SettingsService


async def run(update_bindings: bool) -> None:
    config = load_settings()
    seed = deployment_directory(config)
    if len(seed.entries) > MAX_NODES:
        raise SystemExit(f"节点清单最多支持 {MAX_NODES} 个节点")
    database = Database(config.database.url.get_secret_value())
    try:
        async with database.session() as session:
            await lock_references(session)
            await SettingsService(session, config).site(locked=True)
            current = await registry_record(session, lock=True)
            bindings = {node: target.model_dump() for node, target in seed.bindings.items()}
            if current is None and not update_bindings:
                current = FrontendNodeRegistry(
                    id=1,
                    revision=1,
                    nodes={node: item.model_dump() for node, item in seed.entries.items()},
                    bindings=bindings,
                )
                session.add(current)
                changed = sorted(bindings)
            elif current is not None and update_bindings:
                directory = registry_directory(current)
                changed = []
                for node, target in seed.bindings.items():
                    previous = directory.bindings.get(node)
                    # Public address and enablement belong to the registry after import.
                    fields = {
                        "settings_receiver_url",
                        "nextjs_to_fastapi_token_env",
                        "fastapi_to_nextjs_token_env",
                    }
                    if previous and target.model_dump(include=fields) == previous.model_dump(
                        include=fields
                    ):
                        continue
                    if node in directory.entries and directory.entries[node].enabled:
                        raise SystemExit(f"请先在节点控制台停用 {node} 再更换连接")
                    changed.append(node)
                if not changed:
                    print("部署连接没有变化")
                    return
                current.bindings = current.bindings | {node: bindings[node] for node in changed}
                current.revision += 1
            else:
                raise SystemExit("已导入时请用 --update-bindings；首次导入时请省略此参数")
            session.add(
                AuthAuditEvent(
                    action="frontend_node_import",
                    created_at=datetime.now(UTC),
                    details={
                        "node_ids": changed,
                        "revision": current.revision,
                        "actor": "deployment_cli",
                    },
                )
            )
            await session.commit()
            print(f"节点清单版本 {current.revision}，更新 {len(changed)} 个部署连接")
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-bindings", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.update_bindings))


if __name__ == "__main__":
    main()
