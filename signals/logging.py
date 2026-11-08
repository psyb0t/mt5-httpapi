"""Structured JSON logging to stderr with secret redaction.

Lines carry ``time`` / ``level`` / ``file`` / ``line`` / ``func`` / ``msg``
plus the call site's ``extra`` fields, the same shape as mcpunifier's logs.
The container's stderr is the only sink; Docker keeps and rotates it.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import jsonlogger

_SECRET_RE = re.compile(
    r"(?i)(password|token|secret|api[_-]?key|api[_-]?hash|auth[_-]?key|session|authorization|cookie)"
)
_MAX_REDACT_DEPTH = 8


class RedactingJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that masks values whose key looks like a secret.

    The floor, not the ceiling: call sites still must not pass secrets.
    """

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """ISO 8601 UTC with milliseconds (time.strftime has no %f)."""
        return (
            datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
        )

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        self._redact(log_record)

    def _redact(self, value: Any, depth: int = 0) -> None:
        if depth > _MAX_REDACT_DEPTH:
            return
        if isinstance(value, dict):
            for key in list(value.keys()):
                if _SECRET_RE.search(key):
                    value[key] = "[REDACTED]"
                    continue
                self._redact(value[key], depth + 1)
            return
        if isinstance(value, list):
            for item in value:
                self._redact(item, depth + 1)


def configure_logging(level: str) -> None:
    """Wire JSON logging to stderr."""
    formatter = RedactingJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(funcName)s %(message)s",
        rename_fields={
            "asctime": "time",
            "levelname": "level",
            "filename": "file",
            "lineno": "line",
            "funcName": "func",
            "message": "msg",
        },
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    # httpx logs every request URL at INFO, query strings included.
    logging.getLogger("httpx").setLevel(logging.WARNING)
