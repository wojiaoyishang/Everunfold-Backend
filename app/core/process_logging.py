"""Application logging for the API, Celery worker, and scheduler."""

import json
import logging
import os
import re
import traceback
from datetime import UTC, datetime

from celery import current_task  # type: ignore[import-untyped]


def celery_context(task_id: str | None = None) -> dict[str, object]:
    task = current_task
    if not task:
        return {}
    request = task.request
    if not isinstance(request.id, str) or (task_id is not None and task_id != request.id):
        return {}
    fields: dict[str, object] = {"task_id": request.id[:128], "task_name": task.name[:256]}
    for key in ("root_id", "parent_id", "hostname"):
        if isinstance(value := getattr(request, key, None), str) and value:
            fields["worker_hostname" if key == "hostname" else key] = value[:256]
    if isinstance(retries := request.retries, int):
        fields["retries"] = retries
    if isinstance(queue := (request.delivery_info or {}).get("routing_key"), str) and queue:
        fields["queue"] = queue[:256]
    return fields


def celery_event(record: logging.LogRecord) -> tuple[str, str, dict[str, object]] | None:
    data = getattr(record, "data", None)
    if (
        record.name not in {"celery.worker.strategy", "celery.app.trace"}
        or not isinstance(data, dict)
        or not isinstance(task_id := data.get("id"), str)
        or not isinstance(task_name := data.get("name"), str)
    ):
        return None
    fields = {
        **celery_context(task_id),
        "task_id": task_id[:128],
        "task_name": task_name[:256],
    }
    if record.name == "celery.worker.strategy":
        fields["task_state"] = "RECEIVED"
        return "task_received", "Celery task received", fields

    if isinstance(runtime := data.get("runtime"), (float, int)):
        state = "SUCCESS"
        fields["duration_ms"] = round(runtime * 1000, 3)
    elif isinstance(record.msg, str) and record.msg.endswith("retry: %(exc)s"):
        state = "RETRY"
    elif data.get("description") == "rejected":
        state = "REJECTED"
    elif "exc" not in data or data.get("description") == "ignored":
        state = "IGNORED"
    else:
        state = "FAILURE"
    fields["task_state"] = state
    return "task_finished", f"Celery task {state.lower()}", fields


class RuntimeLogFormatter(logging.Formatter):
    def __init__(self, component: str = "fastapi") -> None:
        super().__init__()
        self.component = component
        self.text = os.environ.get("EVERUNFOLD_LOG_FORMAT") == "text"

    def format(self, record: logging.LogRecord) -> str:
        task = celery_event(record) if self.component == "celery" else None
        message = "" if task else record.getMessage()
        event = "log"
        fields: dict[str, object] = {}
        if task:
            event, message, fields = task
        elif isinstance(http_access := getattr(record, "http_access", None), dict):
            event = "http_access"
            fields = dict(http_access)
        elif record.name == "uvicorn.access" and isinstance(record.args, tuple):
            if len(record.args) == 5:
                _, method, _, _, status = record.args
                event, message = "http_access", f"{method} → {status}"
                fields.update(method=method, status=status)
        elif message.startswith("[Everunfold access] "):
            try:
                data = json.loads(message.removeprefix("[Everunfold access] "))
                if isinstance(data, dict):
                    event = "access"
                    message = str(data.get("reason") or data.get("code") or event)
                    fields = {
                        key: data[key]
                        for key in (
                            "boundary",
                            "code",
                            "peer_ip",
                            "status",
                            "node_id",
                            "client_ip",
                            "client_ip_source",
                            "trusted_proxy",
                            "ip_policy_revision",
                            "x_real_ip",
                            "x_forwarded_for",
                            "forwarded",
                            "cf_connecting_ip",
                            "true_client_ip",
                            "x_forwarded_proto",
                        )
                        if key in data
                    }
            except ValueError:
                pass
        if self.component == "celery" and task is None:
            fields.update(celery_context())
        message = re.sub(r"(://)[^\s/@]+@", r"\1[redacted]@", message)
        message = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [redacted]", message)
        message = re.sub(
            r"""(?i)(https?://[^\s'"?<>]+)\?[^\s'"<>]+""",
            r"\1?[redacted]",
            message,
        )
        if record.exc_info and record.exc_info[0]:
            fields["error_type"] = record.exc_info[0].__name__
            fields["frames"] = [
                f"{os.path.basename(frame.filename)}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(record.exc_info[2])[-12:]
            ]
            if task is None and record.name.startswith("celery."):
                message = f"Celery task or worker failed ({fields['error_type']})"
        if (
            isinstance(symbol := getattr(record, "symbol", None), str)
            and 0 < len(symbol) <= 64
            and symbol[0].isalnum()
            and all(char.isalnum() or char in "_.:-" for char in symbol)
        ):
            fields["symbol"] = symbol
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat()
        level = record.levelname.lower()
        message = message[:16000]
        if self.text:
            message = message.replace("\r", "\\r").replace("\n", "\\n")
            context = {**fields, "pid": record.process}
            details = json.dumps(context, ensure_ascii=False)
            prefix = f"{timestamp} {level} [{self.component}] {event} {record.name}"
            return f"{prefix}: {message} {details}"
        return json.dumps(
            {
                "time": timestamp,
                "component": self.component,
                "level": level,
                "event": event,
                "logger": record.name,
                "pid": record.process,
                "message": message,
                **fields,
            },
            ensure_ascii=False,
        )


def configure_process_logging(
    component: str, variable: str, default: str = "INFO", *, force: bool = False
) -> None:
    level = os.environ.get(variable, default).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"{variable} must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    handler = logging.StreamHandler()
    name = {"API": "fastapi", "Worker": "celery"}.get(component, component.lower())
    handler.setFormatter(RuntimeLogFormatter(name))
    logging.basicConfig(level=level, handlers=[handler], force=force)
