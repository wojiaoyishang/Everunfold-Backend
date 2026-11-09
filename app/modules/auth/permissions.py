"""Stable action names, scoped grants and a shared authenticated business actor."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.modules.auth.models import User

ResourceScope = Literal["own", "all"]
BUILTIN_APP = "everunfold"
CAPABILITIES: dict[str, tuple[str, bool, bool]] = {
    # label, API-key eligible, supports an owner scope
    "pages:read": ("读取页面设置", False, False),
    "pages:write": ("编辑页面草稿", False, False),
    "pages:publish": ("应用与停用页面", False, False),
    "content:read": ("读取内容", True, True),
    "content:write": ("编辑内容", True, True),
    "content:publish": ("直接发布与撤回", True, True),
    "captcha:read_bypass": ("阅读免验证码", True, False),
    "captcha:comment_bypass": ("评论免验证码", True, False),
    "media:read": ("读取媒体库", True, False),
    "media:upload": ("上传媒体", True, False),
    "media:write": ("编辑媒体资料", True, False),
    "media:delete": ("删除与恢复媒体", True, False),
    "shortcodes:read": ("读取短代码", True, False),
    "shortcodes:write": ("管理短代码", True, False),
    "settings:read": ("读取站点设置", True, False),
    "settings:write": ("修改站点设置", True, False),
    "users:manage": ("管理用户与授权", False, False),
    "applications:manage": ("管理API Key接入应用", False, False),
}


def user_permissions(user: User, app_id: str = BUILTIN_APP) -> dict[str, ResourceScope]:
    if not user.active or user.must_change_password:
        return {}
    if user.role == "admin" and app_id == BUILTIN_APP:
        return {name: "all" for name in CAPABILITIES}
    return {
        name: "all" if scope == "all" else "own"
        for name, scope in (user.grants or {}).get(app_id, {}).items()
        if scope in {"own", "all"}
        and (
            app_id != BUILTIN_APP
            or (name in CAPABILITIES and (scope != "own" or CAPABILITIES[name][2]))
        )
    }


def intersect_permissions(
    owner: dict[str, ResourceScope], granted: dict[str, ResourceScope]
) -> dict[str, ResourceScope]:
    return {
        name: "all" if scope == "all" and owner[name] == "all" else "own"
        for name, scope in granted.items()
        if name in owner
    }


@dataclass(frozen=True)
class Actor:
    id: UUID
    username: str
    display_name: str
    role: str
    permissions: dict[str, ResourceScope]
    auth_method: Literal["session", "api_key"] = "session"
    key_id: UUID | None = None
    credential_hash: str | None = None

    def allows(self, capability: str, owner_id: UUID | None = None) -> bool:
        scope = self.permissions.get(capability)
        return scope == "all" or (scope == "own" and owner_id == self.id)

    def has(self, capability: str) -> bool:
        return capability in self.permissions

    @classmethod
    def browser(cls, user: User, credential_hash: str | None = None) -> "Actor":
        return cls(
            user.id,
            user.username,
            user.display_name,
            user.role,
            user_permissions(user),
            credential_hash=credential_hash,
        )


type AuthSubject = Actor | User
