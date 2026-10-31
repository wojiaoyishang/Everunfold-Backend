"""Confirmed, idempotent anonymous likes. No cookie/session or synthetic success."""

import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import UUID4
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.comment_likes_schema import (
    CommentLikeInput,
    CommentLikes,
    CommentLikesQuery,
    CommentLikeState,
)
from app.modules.content.models import Comment, CommentLike, Content
from app.modules.content.repository import public_conditions
from app.modules.content.router import get_session, runtime_from_request
from app.modules.content.router import router as public_router
from app.modules.content.schemas import ErrorResponse, PublicResponse


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/v1/comments",
    tags=["comment-likes"],
    dependencies=[Depends(private_response)],
    responses={
        **public_router.responses,
        403: {"model": ErrorResponse, "description": "来源不允许"},
    },
)


class CommentLikeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(self, ids: list[UUID], visitor: UUID) -> list[CommentLikeState]:
        visitor_hash = hashlib.sha256(str(visitor).encode()).hexdigest()
        statement = (
            select(
                Comment.id,
                func.count(CommentLike.comment_id),
                func.coalesce(func.bool_or(CommentLike.visitor_hash == visitor_hash), False),
            )
            .join(Content, Content.id == Comment.content_id)
            .outerjoin(CommentLike, CommentLike.comment_id == Comment.id)
            .where(Comment.id.in_(ids), Comment.status == "approved", *public_conditions())
            .group_by(Comment.id)
        )
        rows = await self.session.execute(statement)
        states = {
            row[0]: CommentLikeState(comment_id=row[0], count=row[1], liked=row[2]) for row in rows
        }
        return [states[item] for item in dict.fromkeys(ids) if item in states]

    async def set(self, comment_id: UUID, visitor: UUID, liked: bool) -> CommentLikeState:
        try:
            # Serialize changes per comment, including retries from independent API workers.
            visible = await self.session.scalar(
                select(Comment.id)
                .join(Content, Content.id == Comment.content_id)
                .where(Comment.id == comment_id, Comment.status == "approved", *public_conditions())
                .with_for_update(of=Comment)
            )
            if visible is None:
                raise HTTPException(status_code=404, headers={"Cache-Control": "no-store"})
            visitor_hash = hashlib.sha256(str(visitor).encode()).hexdigest()
            if liked:
                await self.session.execute(
                    insert(CommentLike)
                    .values(
                        comment_id=comment_id,
                        visitor_hash=visitor_hash,
                        created_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing()
                )
            else:
                await self.session.execute(
                    delete(CommentLike).where(
                        CommentLike.comment_id == comment_id,
                        CommentLike.visitor_hash == visitor_hash,
                    )
                )
            state = (await self.read([comment_id], visitor))[0]
            # An HTTP acknowledgement must mean the write has actually committed.
            await self.session.commit()
            return state
        except Exception:
            await self.session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]
Visitor = Annotated[UUID4, Header(alias="X-Everunfold-Visitor")]


@router.get("/likes", response_model=PublicResponse[CommentLikes])
async def read_likes(
    query: Annotated[CommentLikesQuery, Query()], session: Session, visitor: Visitor
) -> PublicResponse[CommentLikes]:
    return PublicResponse(
        data=CommentLikes(items=await CommentLikeService(session).read(query.ids, visitor))
    )


@router.put("/{comment_id}/like", response_model=PublicResponse[CommentLikeState])
async def set_like(
    comment_id: UUID,
    body: CommentLikeInput,
    session: Session,
    visitor: Visitor,
    request: Request,
) -> PublicResponse[CommentLikeState]:
    origin = request.headers.get("origin")
    if origin and origin not in runtime_from_request(request).settings.cors.allowed_origins:
        raise HTTPException(status_code=403, headers={"Cache-Control": "no-store"})
    return PublicResponse(
        data=await CommentLikeService(session).set(comment_id, visitor, body.liked)
    )
