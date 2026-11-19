"""Application logging for the API, Celery worker, and scheduler."""

import json
import logging
import os
import re
import traceback
from datetime import UTC, datetime


class RuntimeLogFormatter(logging.Formatter):
    def __init__(self, component: str = "fastapi") -> None:
        super().__init__()
        self.component = component
        self.text = os.environ.get("EVERUNFOLD_LOG_FORMAT") == "text"

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        event = "log"
        fields: dict[str, object] = {}
        if isinstance(http_access := getattr(record, "http_access", None), dict):
            event = "http_access"
            fields = http_access
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
            if record.name.startswith("celery."):
                message = f"Celery task or worker failed ({fields['error_type']})"
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat()
        level = record.levelname.lower()
        message = message[:16000]
        if self.text:
            message = message.replace("\r", "\\r").replace("\n", "\\n")
            line = f"{timestamp} {level} [{self.component}] {event} {record.name}: {message}"
            if fields:
                line += " " + json.dumps(fields, ensure_ascii=False)
            return line
        return json.dumps(
            {
                "time": timestamp,
                "component": self.component,
                "level": level,
                "event": event,
                "logger": record.name,
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
