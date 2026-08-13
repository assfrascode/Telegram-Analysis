import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.observability.context import correlation_ids


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUOTED_CREDENTIAL_RE = re.compile(
    r'''(?i)((?:["'])?(?:authorization|api[_-]?key|password|secret|session|token|phone_code_hash)'''
    r'''(?:["'])?\s*[=:]\s*["'])(.*?)(["'])'''
)
_CREDENTIAL_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|password|secret|session|token|phone_code_hash)"
    r"(\s*[=:]\s*|\"\s*:\s*\")[^\s,;&\"]+"
)
_SAFE_EXTRA_FIELDS = (
    "event",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "subject",
    "durable",
    "stream",
    "outcome",
    "attempts",
    "max_attempts",
    "retry_delay_seconds",
    "reason",
    "dependency",
    "schedule_id",
    "sync_run_id",
    "chat_id",
    "error_type",
    "poll_interval_seconds",
    "concurrency",
    "released_leases",
)


def redact_secrets(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _QUOTED_CREDENTIAL_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]{match.group(3)}", value
    )
    return _CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


class JsonFormatter(logging.Formatter):
    """Emit an allowlisted JSON record without exception messages or request bodies."""

    def format(self, record: logging.LogRecord) -> str:
        ids = correlation_ids()
        document: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        if ids.request_id:
            document["request_id"] = ids.request_id
        if ids.job_id:
            document["job_id"] = ids.job_id
        if ids.task_id:
            document["task_id"] = ids.task_id
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                document[field] = redact_secrets(str(value)) if isinstance(value, str) else value
        if record.exc_info:
            document["exception_type"] = record.exc_info[0].__name__
        return json.dumps(document, separators=(",", ":"), ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Configure application loggers; safe to call again after Uvicorn setup."""
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Uvicorn normally installs non-propagating text handlers before the ASGI
    # lifespan begins. Route them through the same privacy-preserving formatter.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        named_logger = logging.getLogger(logger_name)
        named_logger.handlers.clear()
        named_logger.propagate = True
