"""Public anonymous comment reactions, independent of the synthetic reader preview."""

from uuid import UUID

from pydantic import Field, StrictBool

from app.modules.content.schemas import PublicModel, PublicResponse


class CommentLikesQuery(PublicModel):
    ids: list[UUID] = Field(min_length=1, max_length=50)


class CommentLikeInput(PublicModel):
    liked: StrictBool


class CommentLikeState(PublicModel):
    comment_id: UUID
    liked: bool
    count: int = Field(ge=0)


class CommentLikes(PublicModel):
    items: list[CommentLikeState]


class CommentLikesContract(PublicModel):
    query: CommentLikesQuery
    mutation: CommentLikeInput
    states: PublicResponse[CommentLikes]
    state: PublicResponse[CommentLikeState]
