"""Explicit, repeatable demo seed; never runs at startup or overwrites an edited example."""

import argparse
import asyncio
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.models import User
from app.modules.content.draft_schemas import DraftBlock, DraftCreate, DraftFields
from app.modules.content.draft_service import DraftService

EXAMPLES = (
    (
        "article",
        "一次没有计划的散步",
        "沿着河边走一走，给忙碌的一天留一点空白。",
        "## 傍晚出门\n\n今天没有安排路线，沿着河边慢慢走。路灯亮起的时候，恰好听见一阵风。"
        "\n\n## 想记住的小事\n\n- 路边新开的书店\n- 一杯温热的茶\n- 天边最后一点晚霞",
    ),
    (
        "article",
        "把日常整理成喜欢的样子",
        "从一张桌子开始，重新安排自己的小空间。",
        "最近开始整理桌面。把常用的东西放近一点，把不再需要的东西收好。"
        "\n\n> 好的秩序，应该让人更轻松。\n\n下次想给窗边留一个读书的位置。",
    ),
    ("shuoshuo", "", "", "刚泡好一杯茶，窗外开始下雨。今天就慢一点吧。"),
    (
        "shuoshuo",
        "周末的小记录",
        "",
        "去了好久没去的公园，发现那棵树已经换了颜色。\n\n准备把这些零碎的时刻，好好记下来。",
    ),
)


async def seed(config: Path | None) -> int:
    settings = load_settings(config)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            actor = await session.scalar(
                select(User)
                .where(User.role == "admin", User.active.is_(True))
                .order_by(User.created_at)
                .limit(1)
            )
            if actor is None:
                raise RuntimeError("请先初始化管理员")
            for index, (type_id, title, excerpt, body) in enumerate(EXAMPLES):
                operation_id = uuid5(NAMESPACE_URL, f"everunfold/demo-drafts/v1/{index}")
                await DraftService(session).mutate(
                    "create",
                    DraftCreate(
                        operation_id=operation_id,
                        type_id=type_id,
                        fields=DraftFields(
                            title=title,
                            excerpt=excerpt,
                            body=[DraftBlock(id=uuid5(operation_id, "body"), content=body)],
                        ),
                    ),
                    actor,
                    demo=True,
                )
        return len(EXAMPLES)
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="添加四份示例草稿；重复执行不会覆盖内容")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    print(f"已检查 {asyncio.run(seed(args.config))} 份示例草稿。")
