"""Structured JSON logging with request-scoped attributes and secret redaction.

Log lines carry ``time`` / ``level`` / ``file`` / ``line`` / ``func`` / ``msg``
plus whatever the call site passes through ``extra``. Scope attributes
(``request_id``, ``terminal``) are layered on via a ``ContextVar`` so a call
site does not have to thread them through every signature.
"""

import contextvars
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import jsonlogger

from mcpunifier.constants import LOG_BACKUP_COUNT, LOG_MAX_BYTES

_SECRET_RE = re.compile(
    r"(?i)(password|token|secret|api[_-]?key|authorization|cookie|set-cookie|x-api-key)"
)

_MAX_REDACT_DEPTH = 8

# Default is None rather than {}: a mutable default on a ContextVar is shared
# by every context that never set one, so an accidental in-place update would
# leak across requests. Callers go through get_scope(), which hands back a
# fresh dict.
_scope: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_scope",
    default=None,
)


def with_scope(**attrs: Any) -> contextvars.Token:
    """Layer attributes onto the current scope. Pass the token to ``reset_scope``."""
    return _scope.set({**get_scope(), **attrs})


def reset_scope(token: contextvars.Token) -> None:
    """Pop the scope layer created by the matching ``with_scope`` call."""
    _scope.reset(token)


def get_scope() -> dict[str, Any]:
    """Return the attributes currently in scope."""
    return dict(_scope.get() or {})


class ScopeFilter(logging.Filter):
    """Attach the current scope to every record so handlers emit it."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in get_scope().items():
            setattr(record, key, value)
        return True


class RedactingJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that masks values whose key looks like a secret.

    The floor, not the ceiling: call sites still must not pass secrets. This
    catches the cases nobody planned for — a whole config object logged wide, a
    header map, a third party logging through this logger.
    """

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Emit ISO 8601 UTC with milliseconds.

        logging.Formatter builds timestamps through time.strftime, which has no
        %f directive — a datefmt asking for one emits the literal text "%f"
        instead of the milliseconds. Formatting from the epoch value directly
        avoids that.
        """
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


def configure_logging(level: str, log_path: str) -> None:
    """Wire JSON logging to stderr and a rotating file.

    A file handler that cannot be created is not fatal: the container's stderr
    is the primary sink, and losing the file must not stop the service from
    starting. The failure is reported on the handler that did survive.
    """
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

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    file_error: OSError | None = None
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
            )
        )
    except OSError as err:
        file_error = err

    scope_filter = ScopeFilter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(scope_filter)

    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)

    if file_error is not None:
        logging.getLogger(__name__).warning(
            "file logging unavailable, continuing on stderr only",
            extra={"log_path": log_path, "reason": "log_file_unwritable"},
            exc_info=file_error,
        )
