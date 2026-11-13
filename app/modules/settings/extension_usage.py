"""Advisory use counts from authoritative applied data; never execute plugin code."""

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content
from app.modules.pages.models import ManagedPage
from app.modules.settings.extensions_schemas import (
    ExtensionInventory,
    ExtensionUsage,
    InstalledPlugin,
    PluginUsage,
)
from app.modules.settings.models import FrontendExtensionInventory

_HTML = re.compile(
    r"<!--[\s\S]*?(?:-->|$)|<(pre|code)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
    r"(?:[\s\S]*?</\1\s*>|[\s\S]*$)|</?[a-z][\w:-]*(?:[^>\"']|\"[^\"]*\"|'[^']*')*>",
    re.I,
)
_HEAD = re.compile(r"\{\{<\s*([a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)?)(?=\s|>\}\})")


def _protected(source: str, markdown: bool) -> list[tuple[int, int]]:
    ranges = [(match.start(), match.end()) for match in _HTML.finditer(source)]
    if markdown:
        ranges.extend(
            (match.start(), match.end()) for match in re.finditer(r"(`+)[\s\S]*?\1(?!`)", source)
        )
        fence: tuple[str, int, int] | None = None
        offset = 0
        for line in source.splitlines(keepends=True):
            content = re.sub(r"^(?: {0,3}> ?)+", "", line)
            content = re.sub(r"^ {0,3}(?:[-+*]|\d+[.)]) +", "", content)
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
            if fence:
                if (
                    match
                    and match[1][0] == fence[0]
                    and len(match[1]) >= fence[1]
                    and not match[2].strip()
                ):
                    ranges.append((fence[2], offset + len(line)))
                    fence = None
            elif match:
                fence = (match[1][0], len(match[1]), offset)
            elif re.match(r"^(?: {4}|\t)", content):
                ranges.append((offset, offset + len(line)))
            offset += len(line)
        if fence:
            ranges.append((fence[2], len(source)))
    return sorted(ranges)


def shortcode_references(blocks: object) -> set[str]:
    """Count named native opening markers, excluding escaped and protected source examples."""
    result: set[str] = set()
    if not isinstance(blocks, list):
        return result
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("content"), str):
            continue
        source = block["content"]
        ranges = _protected(source, block.get("type") == "markdown")
        current, consumed = 0, 0
        for match in _HEAD.finditer(source):
            at = match.start()
            if at < consumed:
                continue
            while current < len(ranges) and ranges[current][1] <= at:
                current += 1
            if current < len(ranges) and ranges[current][0] <= at < ranges[current][1]:
                continue
            prefix = source[max(0, at - 64) : at]
            slashes = len(prefix) - len(prefix.rstrip("\\"))
            if slashes % 2:
                continue
            quoted = escaped = False
            for end in range(match.end(), min(len(source), at + 4096)):
                char = source[end]
                if escaped:
                    escaped = False
                elif char == "\\" and quoted:
                    escaped = True
                elif char == '"':
                    quoted = not quoted
                elif not quoted and source.startswith(">}}", end):
                    result.add(match[1])
                    consumed = end + 3
                    break
    return result


async def installed_plugins(session: AsyncSession) -> list[InstalledPlugin]:
    from app.core.plugins.state import installed_info
    from app.modules.content.models import SiteSettings

    site = await session.get(SiteSettings, 1)
    records = await session.scalars(
        select(FrontendExtensionInventory).order_by(FrontendExtensionInventory.node_id)
    )
    return [
        *installed_info(site.data if site else {}),
        *[
            plugin
            for record in records
            for plugin in ExtensionInventory.model_validate(record.data).plugins
        ],
    ]


async def scan_usage(
    session: AsyncSession, definitions: Iterable[InstalledPlugin], plugin_ids: list[str]
) -> ExtensionUsage:
    counts = {
        identity: PluginUsage(plugin_id=identity, title=identity, registered=False)
        for identity in sorted(set(plugin_ids))
    }
    from app.modules.captcha.providers import provider_catalog
    from app.modules.captcha.service import current_config

    captcha = await current_config(session)
    captcha_provider = provider_catalog().get(captcha.provider)
    pages: dict[str, set[str]] = {}
    shortcodes: dict[str, set[str]] = {}
    for plugin in definitions:
        if plugin.id not in counts:
            continue
        counts[plugin.id].registered = True
        counts[plugin.id].title = plugin.title
        if (
            captcha_provider and captcha_provider.widget in plugin.components
        ) or captcha.provider in getattr(plugin, "captcha_providers", []):
            counts[plugin.id].captcha_references = 1
        for identity in plugin.page_ids:
            pages.setdefault(identity, set()).add(plugin.id)
        for name in plugin.shortcodes:
            shortcodes.setdefault(name, set()).add(plugin.id)
    if pages:
        applied = await session.scalars(
            select(ManagedPage.type_id).where(
                ManagedPage.source == "registered",
                ManagedPage.deleted_at.is_(None),
                ManagedPage.applied_snapshot.is_not(None),
                ManagedPage.type_id.in_(pages),
            )
        )
        for type_id in applied:
            for owner in pages[type_id]:
                counts[owner].applied_pages += 1
    if shortcodes:

        async def count_bodies(statement: Select[Any]) -> dict[str, int]:
            # Only bodies are selected; titles, paths, credentials and private metadata stay out.
            totals: dict[str, int] = {}
            stream = await session.stream_scalars(statement)
            async for blocks in stream:
                owners = {
                    owner
                    for name in shortcode_references(blocks)
                    for owner in shortcodes.get(name, ())
                }
                for owner in owners:
                    totals[owner] = totals.get(owner, 0) + 1
            return totals

        published = await count_bodies(
            select(Content.content)
            .where(Content.publication_status == "published")
            .execution_options(yield_per=100)
        )
        scheduled = await count_bodies(
            select(ContentDraft.scheduled_payload["draft"]["body"])
            .where(ContentDraft.deleted_at.is_(None), ContentDraft.scheduled_for.is_not(None))
            .execution_options(yield_per=100)
        )
        for owner, count in published.items():
            counts[owner].published_contents = count
        for owner, count in scheduled.items():
            counts[owner].scheduled_contents = count
    from app.core.jobs.definitions import definitions as task_definitions
    from app.core.jobs.models import TaskRun

    owners = {key: owner for key, (owner, _) in task_definitions().items()}
    for task in await session.scalars(
        select(TaskRun.task_id).where(TaskRun.status.in_(["queued", "running"]))
    ):
        task_owner = owners.get(task)
        if task_owner is not None and task_owner in counts:
            counts[task_owner].active_tasks += 1
    return ExtensionUsage(scanned_at=datetime.now(UTC), plugins=list(counts.values()))
