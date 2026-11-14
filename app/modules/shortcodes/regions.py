"""Publish opaque region markers and retain bodies only in the private reader store."""

import base64
import hmac
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, overload
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import ForeignKey, String, Text, delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth.service import AuthFailure
from app.modules.content.component_document import ComponentDocument
from app.modules.content.editor_schema import EditorSettings

RegionKind = Literal["reply-only", "password-area"]
SourceType = Literal["markdown", "html"]
REGION_TOKEN = re.compile(r"{{<\s*(/?)\s*(reply-only|password-area)(?=[\s/>}]|$)")
PUBLIC_REGION = re.compile(r'{{< (reply-only|password-area) region="[a-f0-9-]{36}" >}}{{< /\1 >}}')
REGION_END = re.compile(r"\s*>}}")
PASSWORD_ARGUMENT = re.compile(r'\s+credential="([a-f0-9-]{36})"\s*>}}')


def without_region_markers(source: str) -> str:
    return PUBLIC_REGION.sub("", source)


def require_no_regions(value: object) -> None:
    if isinstance(value, str):
        if REGION_TOKEN.search(value):
            raise AuthFailure(422, 42284, "可见区域只能放在正文或短代码组件中")
    elif isinstance(value, dict):
        for key, entry in value.items():
            require_no_regions(key)
            require_no_regions(entry)
    elif isinstance(value, (list, tuple)):
        for entry in value:
            require_no_regions(entry)


class RegionPassword(Base):
    __tablename__ = "shortcode_region_passwords"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)


class ShortcodeRegion(Base):
    __tablename__ = "shortcode_regions"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    owner_kind: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(12))
    body: Mapped[str] = mapped_column(Text)
    media_urls: Mapped[dict[str, Any]] = mapped_column(JSONB)
    credential: Mapped[UUID | None] = mapped_column(
        ForeignKey("shortcode_region_passwords.id", ondelete="RESTRICT"),
    )


@dataclass(frozen=True)
class Region:
    id: UUID
    kind: str
    body: str
    source_type: SourceType
    credential: UUID | None


def invalid_region() -> AuthFailure:
    return AuthFailure(
        422,
        42281,
        "可见区域需要配对的开始与结束标记，不能嵌套。密码区域须先配置口令。",
    )


def split_regions(
    source: str,
    source_type: SourceType,
    identity: Callable[[], UUID] = uuid4,
) -> tuple[str, list[Region]]:
    """Pair authoring tokens before creating any cache, excerpt, media or search data."""
    fragments: list[Region] = []
    output: list[str] = []
    pending: tuple[re.Match[str], int, UUID | None] | None = None
    cursor = 0
    for token in REGION_TOKEN.finditer(source):
        closing, kind = token.groups()
        tail_pattern = PASSWORD_ARGUMENT if kind == "password-area" and not closing else REGION_END
        tail = tail_pattern.match(source, token.end())
        if tail is None:
            raise invalid_region()
        if not closing:
            if pending is not None:
                raise invalid_region()
            credential = None
            if tail_pattern is PASSWORD_ARGUMENT:
                try:
                    credential = UUID(tail[1])
                except ValueError:
                    raise invalid_region() from None
            pending = (token, tail.end(), credential)
            continue
        if pending is None or pending[0][2] != kind:
            raise invalid_region()
        opening, body_start, credential = pending
        fragment = Region(
            identity(), kind, source[body_start : token.start()], source_type, credential
        )
        fragments.append(fragment)
        output.extend(
            (
                source[cursor : opening.start()],
                f'{{{{< {kind} region="{fragment.id}" >}}}}{{{{< /{kind} >}}}}',
            )
        )
        cursor = tail.end()
        pending = None
    if pending is not None:
        raise invalid_region()
    return "".join(output) + source[cursor:], fragments


def region_cipher(config: Settings, identity: UUID) -> Fernet:
    master = os.environ.get(config.auth.application_master_key_env, "")
    if not 43 <= len(master) <= 256:
        raise AuthFailure(503, 50381, "服务器尚未配置密码区域加密密钥")
    digest = hmac.digest(
        master.encode(), b"everunfold.shortcode.region.v1:" + identity.bytes, "sha256"
    )
    return Fernet(base64.urlsafe_b64encode(digest))


def open_body(config: Settings, region: ShortcodeRegion) -> str:
    if region.kind == "reply-only":
        return region.body
    try:
        return region_cipher(config, region.id).decrypt(region.body.encode()).decode()
    except InvalidToken:
        raise AuthFailure(503, 50382, "暂时无法解密此区域") from None


class RegionPublication:
    def __init__(
        self,
        session: AsyncSession,
        config: Settings,
        owner_kind: Literal["content", "page"],
        owner_id: UUID,
    ) -> None:
        self.session = session
        self.config = config
        self.owner_kind = owner_kind
        self.owner_id = owner_id
        self.fragments: list[Region] = []

    def redact(self, source: str, source_type: SourceType) -> str:
        public_source, fragments = split_regions(source, source_type)
        self.fragments.extend(fragments)
        if len(self.fragments) > 100:
            raise AuthFailure(422, 42282, "一份内容最多可包含 100 个可见区域")
        return public_source

    @overload
    def redact_document(self, document: ComponentDocument) -> ComponentDocument: ...

    @overload
    def redact_document(self, document: None) -> None: ...

    def redact_document(self, document: ComponentDocument | None) -> ComponentDocument | None:
        if document is None:
            return None
        redacted = document.transform_sources(
            lambda node, source: self.redact(
                source, "html" if node.type == "core.html" else "markdown"
            )
        )
        for node, _ in redacted.walk():
            props = node.props
            if node.type in {"core.html", "core.markdown", "core.shortcode"}:
                props = {key: value for key, value in props.items() if key != "source"}
            require_no_regions(props)
        return redacted

    async def store(self) -> None:
        from app.modules.content.editor_service import editor_media

        await self.session.execute(
            delete(ShortcodeRegion).where(
                ShortcodeRegion.owner_kind == self.owner_kind,
                ShortcodeRegion.owner_id == self.owner_id,
            )
        )
        for fragment in self.fragments:
            body = fragment.body
            if fragment.credential:
                if await self.session.get(RegionPassword, fragment.credential) is None:
                    raise AuthFailure(422, 42283, "此区域的口令配置不存在，请重新设置")
                body = region_cipher(self.config, fragment.id).encrypt(body.encode()).decode()
            media = await editor_media(self.session, self.config, fragment.body, EditorSettings())
            self.session.add(
                ShortcodeRegion(
                    id=fragment.id,
                    owner_id=self.owner_id,
                    owner_kind=self.owner_kind,
                    kind=fragment.kind,
                    source_type=fragment.source_type,
                    body=body,
                    media_urls={name: entry.model_dump() for name, entry in media.items()},
                    credential=fragment.credential,
                )
            )
