"""Emit structured business events through the standard runtime logger."""

import logging
from dataclasses import asdict

from app.core.process_logging import BUSINESS_LOGGER
from app.plugin_sdk import BusinessEvent, PluginRegistry, define_plugin

logger = logging.getLogger(BUSINESS_LOGGER)
HTTP_REASONS = {
    401: "authentication",
    403: "forbidden",
    404: "unavailable",
    409: "conflict",
    413: "payload_limit",
    415: "unsupported_media",
    422: "validation",
    429: "rate_limited",
}


def log_event(event: BusinessEvent) -> None:
    reason = event.reason
    if event.result == "failure" and event.status is not None:
        reason = HTTP_REASONS.get(
            event.status, "server_error" if event.status >= 500 else "rejected"
        )
    fields = {
        key: value for key, value in asdict(event).items() if value is not None and value != ""
    }
    if reason:
        fields["reason"] = reason
    if event.resource_id is not None:
        fields["resource_id"] = str(event.resource_id)
    level = (
        logging.ERROR
        if event.status is not None and event.status >= 500 and event.result == "failure"
        else logging.WARNING
        if event.result in {"failure", "unknown"}
        else logging.INFO
    )
    logger.log(
        level,
        "%s %s",
        event.action,
        event.result,
        extra={
            "business_event": {
                key if key in {"request_id", "status", "resource_id"} else "business_" + key: value
                for key, value in fields.items()
            },
            "symbol": f"business.{event.action}.{event.result}",
        },
    )


def setup(registry: PluginRegistry) -> None:
    registry.business_events.register(log_event)


plugin = define_plugin(
    id="core.business-observability",
    title="业务事件日志",
    description="记录登录、评论、内容和通知等业务结果，供 Loki 与 Grafana 统计。",
    author="Everunfold",
    setup=setup,
)
