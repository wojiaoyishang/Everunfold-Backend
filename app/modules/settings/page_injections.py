"""Site-wide code rules controlled by the administrator browser session."""

from typing import Any

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.cache_events import stage_cache_events
from app.modules.settings.defaults import public_settings
from app.modules.settings.extensions_schemas import ExtensionPreferences
from app.modules.settings.models import SettingsAudit
from app.modules.settings.node_registry import read_targets
from app.modules.settings.page_injection_schemas import (
    PLUGIN_ID,
    STORE_KEY,
    PageInjectionRules,
    PageInjectionState,
    SavePageInjections,
    Surface,
)
from app.modules.settings.service import SettingsService


def require_code_admin(actor: Actor) -> None:
    if actor.role != "admin" or actor.auth_method != "session":
        raise AuthFailure(403, 40364, "只有通过浏览器登录的站点管理员能管理页面代码")


def configured_rules(data: dict[str, Any]) -> PageInjectionState:
    return PageInjectionState.model_validate(data.get(STORE_KEY, {}))


def effective_rules(data: dict[str, Any], surface: Surface) -> PageInjectionRules:
    if not ExtensionPreferences.model_validate(data.get("extensions", {})).enables(PLUGIN_ID):
        return PageInjectionRules()
    return PageInjectionRules(
        rules=[
            rule
            for rule in configured_rules(data).rules
            if rule.enabled and rule.can_target(surface)
        ]
    )


async def save_rules(
    service: SettingsService, actor: Actor, change: SavePageInjections
) -> PageInjectionState:
    require_code_admin(actor)
    session = service.session
    await lock_references(session)
    record = await service.site(locked=True)
    previous = configured_rules(record.data)
    if previous.revision != change.expected_revision:
        raise AuthFailure(409, 40920, "页面代码已被修改，请重新读取后合并更改")
    if previous.rules == change.rules:
        return previous
    before_public = public_settings(record.data).page_injections
    saved = PageInjectionState(revision=previous.revision + 1, rules=change.rules)
    record.data = {**record.data, STORE_KEY: saved.model_dump(mode="json")}
    resolved = public_settings(record.data)
    if resolved.page_injections != before_public:
        resolved = await service.resolve(resolved)
        record.generation += 1
        await stage_cache_events(
            session,
            record,
            "public-settings",
            record.generation,
            (CacheChange("settings_changed"),),
        )
        for node, target in (await read_targets(session, service.config)).items():
            if target.enabled and target.settings_receiver_url:
                await service.enqueue(
                    node, record.source_epoch, "public-settings", record.generation, resolved
                )
    session.add(
        SettingsAudit(
            user_id=actor.id,
            resource="page-injections",
            revision=saved.revision,
            changes={"rules": [rule.id for rule in saved.rules]},
        )
    )
    await session.commit()
    return saved
