"""A layout belongs to the authenticated actor; optimistic revisions prevent lost edits."""

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.comments.schemas import WorkspaceLayoutValue
from app.modules.comments.workspace_models import WorkspaceLayout


async def read_layout(session: AsyncSession, actor: Actor) -> WorkspaceLayoutValue:
    row = await session.get(WorkspaceLayout, actor.id)
    return (
        WorkspaceLayoutValue.model_validate({"revision": row.revision, "cards": row.cards})
        if row
        else WorkspaceLayoutValue()
    )


async def save_layout(
    session: AsyncSession, actor: Actor, value: WorkspaceLayoutValue
) -> WorkspaceLayoutValue:
    if actor.auth_method != "session":
        raise AuthFailure(403, 40350, "请在浏览器登录后操作")
    cards = [card.model_dump() for card in value.cards] if value.cards is not None else None
    # JSON null means default layout; an empty array is an intentionally empty board.
    if value.revision == 0:
        changed = await session.scalar(
            insert(WorkspaceLayout)
            .values(user_id=actor.id, revision=1, cards=cards)
            .on_conflict_do_nothing(index_elements=[WorkspaceLayout.user_id])
            .returning(WorkspaceLayout.revision)
        )
    else:
        changed = await session.scalar(
            update(WorkspaceLayout)
            .where(WorkspaceLayout.user_id == actor.id, WorkspaceLayout.revision == value.revision)
            .values(cards=cards, revision=value.revision + 1)
            .returning(WorkspaceLayout.revision)
        )
    if changed is None:
        await session.rollback()
        raise AuthFailure(409, 40950, "工作台布局已更新，请刷新后重试")
    await session.commit()
    return WorkspaceLayoutValue(revision=changed, cards=value.cards)
