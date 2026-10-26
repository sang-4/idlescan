"""Optional stdlib logging with redaction and a JSON formatter.

The application records durable audit rows in SQLite.  This module is only for
process logs (startup, HTTP requests, background failures): text by default,
one-object-per-line JSON when ``IDLESCAN_LOG_FORMAT=json`` is set.  Secrets are
scrubbed before a record leaves the process.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .redaction import redact_text

_CONFIGURED = False


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact_text(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_text(value) if isinstance(value, str) else value
                    for value in record.args
                )
        if record.exc_info and record.exc_info[1] is not None:
            record.msg = f"{record.msg} ({record.exc_info[1].__class__.__name__})"
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO", fmt: str = "text", *, stream=None, force: bool = False
) -> None:
    """Configure the root logger once; safe to call from tests with ``force``."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(RedactingFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    _CONFIGURED = True


def get_logger(name: str = "idlescan") -> logging.Logger:
    return logging.getLogger(name)
