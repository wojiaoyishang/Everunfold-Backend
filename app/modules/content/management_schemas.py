"""Bounded metadata commands; body and publication remain separate operations."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import PublicModel
from app.modules.content.editor_schema import EditorSettings


class TermChange(PublicModel):
    mode: Literal["add", "remove", "replace"] = "replace"
    values: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> Self:
        self.values = EditorSettings(tags=self.values).tags
        return self


class MetadataPatch(PublicModel):
    title: str | None = Field(default=None, max_length=200)
    excerpt: str | None = Field(default=None, max_length=1000)
    categories: TermChange | None = None
    tags: TermChange | None = None
    pinned: bool | None = None
    comments_open: bool | None = None


class DraftVersion(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=1)
    expected_seq: int | None = Field(default=None, ge=0)


class DraftBatch(PublicModel):
    operation_id: UUID
    items: list[DraftVersion] = Field(min_length=1, max_length=50)
    action: Literal["edit", "trash", "restore"]
    patch: MetadataPatch | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("重复的内容ID")
        if self.action == "edit":
            if not self.patch or not self.patch.model_dump(exclude_none=True):
                raise ValueError("请选择需要修改的字段")
            if len(self.items) > 1 and (
                self.patch.title is not None or self.patch.excerpt is not None
            ):
                raise ValueError("标题和摘要仅支持单篇快速编辑")
        elif self.patch:
            raise ValueError("此操作不接受编辑字段")
        return self


class TaxonomyQuery(PublicModel):
    kind: Literal["category", "tag"] = "category"
    search: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class TaxonomyItem(PublicModel):
    id: UUID
    kind: Literal["category", "tag"]
    name: str
    slug: str
    parent_id: UUID | None
    revision: int
    published_count: int
    draft_count: int


class TaxonomyCatalog(PublicModel):
    items: list[TaxonomyItem]
    parents: list[TaxonomyItem]
    total: int
    page: int
    page_size: int


class TaxonomyCommand(PublicModel):
    operation_id: UUID
    action: Literal["create", "update", "merge", "remove"]
    kind: Literal["category", "tag"] = "category"
    id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    name: str = Field(default="", max_length=60)
    parent_id: UUID | None = None
    target_id: UUID | None = None
    expected_target_revision: int | None = Field(default=None, ge=1)
    impact_token: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.action == "create" and (self.id or self.expected_revision):
            raise ValueError("新增分类不能指定已有ID或版本")
        if self.action in {"create", "update"}:
            names = EditorSettings(tags=[self.name]).tags
            if not names:
                raise ValueError("请填写名称")
            self.name = names[0]
        if self.action != "merge" and (self.target_id or self.expected_target_revision):
            raise ValueError("仅合并操作接受目标分类")
        if self.action in {"merge", "remove"} and (self.name or self.parent_id):
            raise ValueError("合并和删除不接受名称或父级修改")
        if self.action != "create" and (not self.id or not self.expected_revision):
            raise ValueError("缺少分类版本")
        if self.action == "merge" and (
            not self.target_id or self.target_id == self.id or not self.expected_target_revision
        ):
            raise ValueError("请选择另一项合并目标及其版本")
        if self.kind == "tag" and self.parent_id:
            raise ValueError("标签不支持父级")
        return self


class TaxonomyImpact(PublicModel):
    token: str
    drafts: int
    published: int
    scheduled: int
    children: int


class TaxonomyResult(PublicModel):
    id: UUID
    action: str
    updated_at: datetime
