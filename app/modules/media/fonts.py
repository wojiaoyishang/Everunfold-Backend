"""Validated public font originals; family names are stable media identities."""

import io
import re
from typing import Any, Literal
from uuid import UUID

from fontTools.ttLib import TTFont, TTLibError  # type: ignore[import-untyped]
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.schemas import PublicModel
from app.modules.auth.service import AuthFailure
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import IconAsset, IconQuery

MAX_FONT_BYTES = 20 * 1024 * 1024


class FontInfo(PublicModel):
    family: str
    style: Literal["normal", "italic"]
    weight_min: int = Field(ge=1, le=1000)
    weight_max: int = Field(ge=1, le=1000)
    format: Literal["woff2", "woff", "truetype", "opentype"]


class FontAsset(IconAsset):
    info: FontInfo
    css_family: str


class FontCatalog(PublicModel):
    items: list[FontAsset]
    page: int
    total: int
    page_size: int = 60


def inspect_font(data: bytes) -> tuple[str, str, dict[str, Any]]:
    formats: dict[bytes, tuple[str, str, Literal["woff2", "woff", "truetype", "opentype"]]] = {
        b"wOF2": ("font/woff2", "woff2", "woff2"),
        b"wOFF": ("font/woff", "woff", "woff"),
        b"OTTO": ("font/otf", "otf", "opentype"),
        b"\x00\x01\x00\x00": ("font/ttf", "ttf", "truetype"),
    }
    signature = data[:4]
    if not data or len(data) > MAX_FONT_BYTES:
        raise AuthFailure(413, 41331, "字体文件不超过 20 MB")
    if signature not in formats:
        raise AuthFailure(415, 41531, "请选择有效的 WOFF2、WOFF、TTF 或 OTF 字体")
    if signature in {b"wOF2", b"wOFF"} and int.from_bytes(data[16:20], "big") > 64 * 1024 * 1024:
        raise AuthFailure(413, 41331, "解压后的字体过大")
    try:
        with TTFont(io.BytesIO(data), lazy=True) as font:
            if not {"head", "maxp", "name", "cmap"}.issubset(font.keys()):
                raise ValueError("incomplete font")
            if not 0 < font["maxp"].numGlyphs <= 200_000 or not font.getBestCmap():
                raise ValueError("empty font")
            names = font["name"]
            family = names.getDebugName(16) or names.getDebugName(1) or "未命名字体"
            family = re.sub(r"[\x00-\x1f\x7f]", "", family).strip()[:160]
            weight = max(1, min(1000, int(font["OS/2"].usWeightClass))) if "OS/2" in font else 400
            low, high = weight, weight
            if "fvar" in font:
                for axis in font["fvar"].axes:
                    if axis.axisTag == "wght":
                        low, high = max(1, int(axis.minValue)), min(1000, int(axis.maxValue))
            italic = "post" in font and font["post"].italicAngle != 0
            mime, extension, kind = formats[signature]
            info = FontInfo(
                family=family,
                style="italic" if italic else "normal",
                weight_min=low,
                weight_max=high,
                format=kind,
            )
            return mime, extension, info.model_dump()
    except (TTLibError, ValueError, KeyError, OSError, EOFError, AssertionError) as exc:
        raise AuthFailure(422, 42232, "字体文件损坏或无法读取") from exc


def font_dto(asset: MediaAsset, settings: Settings) -> FontAsset:
    return FontAsset(
        id=f"media:{asset.id}",
        label=asset.title or asset.filename,
        url=settings.media_base_url + "/" + asset.object_key,
        mime=asset.mime,
        size=asset.size,
        created_at=asset.created_at,
        info=FontInfo.model_validate(asset.font_info),
        css_family="ef-font-" + str(asset.id),
    )


async def query_fonts(session: AsyncSession, settings: Settings, query: IconQuery) -> FontCatalog:
    where = [
        MediaAsset.status == "ready",
        MediaAsset.deleted_at.is_(None),
        MediaAsset.font_info.is_not(None),
    ]
    if query.query.strip():
        term = query.query.strip()
        if re.fullmatch(r"media:[0-9a-f-]{36}", term):
            where.append(MediaAsset.id == UUID(term[6:]))
        else:
            where.append(
                or_(
                    MediaAsset.filename.icontains(term, autoescape=True),
                    MediaAsset.title.icontains(term, autoescape=True),
                )
            )
    total = await session.scalar(select(func.count()).select_from(MediaAsset).where(*where)) or 0
    assets = await session.scalars(
        select(MediaAsset)
        .where(*where)
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id)
        .offset((query.page - 1) * 60)
        .limit(60)
    )
    return FontCatalog(
        items=[font_dto(asset, settings) for asset in assets], page=query.page, total=total
    )


async def font_stylesheet(session: AsyncSession, settings: Settings) -> str:
    assets = await session.scalars(
        select(MediaAsset)
        .where(
            MediaAsset.status == "ready",
            MediaAsset.deleted_at.is_(None),
            MediaAsset.font_info.is_not(None),
        )
        .order_by(MediaAsset.id)
    )
    rules = []
    for asset in assets:
        font = font_dto(asset, settings)
        # URL is configured, never assembled from a supplied font name or a browser CSS string.
        url = font.url.replace("\\", "%5C").replace('"', "%22").replace("\n", "").replace("\r", "")
        info = font.info
        rules.append(
            f'@font-face{{font-family:"{font.css_family}";'
            f'src:url("{url}") format("{info.format}");'
            f"font-weight:{info.weight_min} {info.weight_max};"
            f"font-style:{info.style};font-display:swap;}}"
        )
    return "\n".join(rules)
