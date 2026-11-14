"""Versioned records for the administrator's site maintenance tools."""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.core.schemas import PublicModel, PublicResponse

MaintenanceKind = Literal[
    "article", "shuoshuo", "page", "glossary", "category", "tag", "comment", "template", "site"
]
MaintenanceStatus = Literal["draft", "published", "archived"]
MaintenanceState = Literal["create", "skip", "conflict", "blocked"]

MAX_ITEMS = 2000
MAX_BUNDLE_BYTES = 16 * 1024 * 1024


class MaintenanceBundleItem(PublicModel):
    kind: MaintenanceKind
    id: UUID
    title: str = Field(max_length=1000)
    status: MaintenanceStatus
    updated_at: AwareDatetime
    value: dict[str, Any]


class MaintenanceBundle(PublicModel):
    format: Literal["everunfold-content"]
    version: Literal[1]
    exported_at: AwareDatetime
    items: list[MaintenanceBundleItem] = Field(max_length=MAX_ITEMS)


class MaintenanceExportRequest(PublicModel):
    kinds: list[MaintenanceKind] = Field(min_length=1, max_length=9)
    statuses: list[MaintenanceStatus] | None = Field(default=None, max_length=3)
    date_from: AwareDatetime | None = None
    date_to: AwareDatetime | None = None


class MaintenanceImportRequest(PublicModel):
    bundle: MaintenanceBundle


class MaintenanceApplyRequest(MaintenanceImportRequest):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class MaintenanceImportItem(PublicModel):
    kind: MaintenanceKind
    id: UUID
    title: str
    state: MaintenanceState
    reason: str | None


class MaintenanceCounts(PublicModel):
    create: int
    skip: int
    conflict: int
    blocked: int


class MaintenancePreview(PublicModel):
    digest: str
    items: list[MaintenanceImportItem]
    counts: MaintenanceCounts


class MaintenanceReceipt(PublicModel):
    id: UUID
    created_at: AwareDatetime
    counts: MaintenanceCounts


class MaintenanceHistory(PublicModel):
    items: list[MaintenanceReceipt]


class MaintenanceHealthCheck(PublicModel):
    id: Literal["database", "redis", "queue", "media", "mail", "query_cache"]
    status: Literal["ok", "warning", "unavailable", "not_configured"]
    detail: str | None


class MaintenanceHealth(PublicModel):
    checked_at: AwareDatetime
    checks: list[MaintenanceHealthCheck]


class MaintenanceContract(PublicModel):
    export_request: MaintenanceExportRequest
    bundle: PublicResponse[MaintenanceBundle]
    import_request: MaintenanceImportRequest
    apply_request: MaintenanceApplyRequest
    preview: PublicResponse[MaintenancePreview]
    receipt: PublicResponse[MaintenanceReceipt]
    history: PublicResponse[MaintenanceHistory]
    health: PublicResponse[MaintenanceHealth]
