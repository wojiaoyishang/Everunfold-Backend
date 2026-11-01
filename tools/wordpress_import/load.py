"""Explicitly load a prepared public dataset and atomically export a public snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text

from app.core.config import load_settings
from app.core.database import Database
from app.modules.content.models import (
    Comment,
    Content,
    ContentTaxonomy,
    SearchDocument,
    SiteSettings,
    Taxonomy,
)
from app.modules.content.repository import ContentRepository
from app.modules.content.schemas import ContentDetail, PublicComment, SiteDTO, TaxonomyDTO
from app.modules.content.service import PublicContentService
from tools.wordpress_import.files import write_text_atomic
from tools.wordpress_import.text import plain_text


def _set_fields(instance: Any, fields: dict[str, Any]) -> bool:
    changed = False
    for name, value in fields.items():
        if getattr(instance, name) != value:
            setattr(instance, name, value)
            changed = True
    return changed


def _without_legacy(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "legacy_id"}


async def load_public(input_path: Path, config_path: Path) -> dict[str, int]:
    """One caller-owned transaction; repeat input leaves IDs, rows and generation stable."""
    if input_path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Prepared input exceeds 32 MiB")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if payload.get("format") != "everunfold-wordpress-public-1":
        raise ValueError("Unsupported prepared dataset format")
    site_data = SiteDTO.model_validate(payload["site"]).model_dump(mode="json", exclude={"stats"})
    settings = load_settings(config_path)
    database = Database(settings.database.url.get_secret_value())
    changed = False
    inserted = 0
    updated = 0
    fields: dict[str, Any]
    try:
        async with database.session_factory() as session, session.begin():
            from app.modules.media.locking import lock_references

            await lock_references(session)
            # Serialize explicit importer runs; no lock is shared with normal API reads.
            await session.execute(text("SELECT pg_advisory_xact_lock(792130143)"))
            if not await database.schema_is_current():
                raise ValueError("Apply the explicit schema migration before import")
            for raw in payload["taxonomies"]:
                taxonomy_dto = TaxonomyDTO.model_validate(_without_legacy(raw))
                identity = UUID(taxonomy_dto.id)
                taxonomy_row = await session.get(Taxonomy, identity)
                fields = {
                    "source_id": int(raw["legacy_id"]),
                    "kind": taxonomy_dto.kind,
                    "slug": taxonomy_dto.slug,
                    "name": taxonomy_dto.name,
                }
                if taxonomy_row is None:
                    session.add(Taxonomy(id=identity, **fields))
                    inserted += 1
                    changed = True
                elif _set_fields(taxonomy_row, fields):
                    updated += 1
                    changed = True
            await session.flush()
            desired_links: set[tuple[UUID, UUID]] = set()
            imported_ids: list[UUID] = []
            for raw in payload["contents"]:
                clean = _without_legacy(raw)
                clean["categories"] = [
                    _without_legacy(content_dto) for content_dto in clean["categories"]
                ]
                clean["tags"] = [_without_legacy(content_dto) for content_dto in clean["tags"]]
                content_dto = ContentDetail.model_validate(clean)
                identity = UUID(content_dto.id)
                imported_ids.append(identity)
                fields = {
                    "source": "wordpress",
                    "source_id": int(raw["legacy_id"]),
                    "kind": content_dto.kind,
                    "title": content_dto.title,
                    "path": content_dto.path,
                    "excerpt": content_dto.excerpt,
                    "language": content_dto.language,
                    "published_at": content_dto.published_at,
                    "updated_at": content_dto.updated_at,
                    "author": content_dto.author,
                    "cover_url": content_dto.cover_url,
                    "reading_minutes": content_dto.reading_minutes,
                    "word_count": content_dto.word_count,
                    "pinned": content_dto.pinned,
                    "publication_status": "published",
                    "visibility": "public",
                    "content": [block.model_dump(mode="json") for block in content_dto.content],
                    "media_urls": {
                        key: value.model_dump(mode="json", exclude_defaults=True)
                        for key, value in content_dto.media_urls.items()
                    },
                }
                content_row = await session.get(Content, identity)
                if content_row is None:
                    session.add(Content(id=identity, public_generation=1, **fields))
                    inserted += 1
                    changed = True
                elif _set_fields(content_row, fields):
                    content_row.public_generation += 1
                    updated += 1
                    changed = True
                desired_links.update(
                    (identity, UUID(taxonomy.id))
                    for taxonomy in [*content_dto.categories, *content_dto.tags]
                )
                search_fields = {
                    "title": content_dto.title,
                    "body": "\n".join(plain_text(block.content) for block in content_dto.content),
                }
                search = await session.get(SearchDocument, identity)
                if search is None:
                    await session.flush()
                    session.add(SearchDocument(content_id=identity, **search_fields))
                else:
                    changed = _set_fields(search, search_fields) or changed
            await session.flush()
            link_rows = await session.execute(
                select(ContentTaxonomy.content_id, ContentTaxonomy.taxonomy_id).where(
                    ContentTaxonomy.content_id.in_(imported_ids)
                )
            )
            current_links = {(content_id, taxonomy_id) for content_id, taxonomy_id in link_rows}
            if current_links != desired_links:
                await session.execute(
                    delete(ContentTaxonomy).where(ContentTaxonomy.content_id.in_(imported_ids))
                )
                session.add_all(
                    ContentTaxonomy(content_id=content_id, taxonomy_id=taxonomy_id)
                    for content_id, taxonomy_id in sorted(desired_links)
                )
                changed = True
            parent_links: list[tuple[Comment, UUID | None]] = []
            for content_id, rows in payload["comments"].items():
                if UUID(content_id) not in imported_ids:
                    raise ValueError("Comment references a content outside the prepared dataset")
                valid_parents = {raw["id"] for raw in rows}
                for raw in rows:
                    comment_dto = PublicComment.model_validate(_without_legacy(raw))
                    if comment_dto.parent_id and comment_dto.parent_id not in valid_parents:
                        raise ValueError("Comment parent is not public in the same content")
                    identity = UUID(comment_dto.id)
                    parent = UUID(comment_dto.parent_id) if comment_dto.parent_id else None
                    fields = {
                        "source_id": int(raw["legacy_id"]),
                        "content_id": UUID(content_id),
                        "author": comment_dto.author,
                        "content": comment_dto.content,
                        "created_at": comment_dto.created_at,
                        "status": "approved",
                    }
                    comment_row = await session.get(Comment, identity)
                    if comment_row is None:
                        comment_row = Comment(id=identity, parent_id=None, **fields)
                        session.add(comment_row)
                        inserted += 1
                        changed = True
                    elif _set_fields(comment_row, fields):
                        updated += 1
                        changed = True
                    parent_links.append((comment_row, parent))
            await session.flush()
            for comment_row, parent in parent_links:
                changed = _set_fields(comment_row, {"parent_id": parent}) or changed
            stored = await session.get(SiteSettings, 1)
            if stored is None:
                stored = SiteSettings(id=1, data=site_data, generation=1)
                session.add(stored)
                changed = True
            else:
                changed = _set_fields(stored, {"data": site_data}) or changed
                if changed:
                    stored.generation += 1
                    stored.content_generation += 1
            await session.flush()
            generation = stored.generation
        return {
            "inserted": inserted,
            "updated": updated,
            "generation": generation,
            "changed": int(changed),
        }
    finally:
        await database.close()


async def export_snapshot(output: Path, config_path: Path) -> dict[str, int]:
    settings = load_settings(config_path)
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            snapshot = await PublicContentService(ContentRepository(session)).snapshot()
            serialized = snapshot.model_dump_json(indent=2)
        write_text_atomic(output, serialized)
        return {
            "generation": snapshot.generation,
            "contents": len(snapshot.contents),
            "bytes": len(serialized.encode("utf-8")),
        }
    finally:
        await database.close()


async def run(args: argparse.Namespace) -> None:
    result: dict[str, Any] = {}
    if args.input:
        result["import"] = await load_public(args.input, args.config)
    if args.export:
        result["export"] = await export_snapshot(args.export, args.config)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--export", type=Path, help="Destination frontend current.json snapshot")
    args = parser.parse_args()
    if not args.input and not args.export:
        parser.error("provide --input and/or --export")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
