"""Imperative one-shot command channel (screenshot, forced refresh).

Deploy/stop are NOT commands — they are desired-state edits handled by
registry.py. This channel exists only for operations that produce a
side-effect artifact rather than converge state.

Protocol: API writes command.json {command_id, action, ...}; loader
executes, writes command_result.json {command_id, status, ...}, deletes
command.json. API polls for the result up to a timeout. One command in
flight at a time, serialized by a lock (matching the loader's
one-command contract).
"""
import json
import os
import secrets
import threading
import time

from mt5api.chartctl import paths
from mt5api.config import CHARTCTL_COMMAND_TIMEOUT_SECONDS

_LOCK = threading.Lock()


class LoaderTimeout(TimeoutError):
    pass


class LoaderBusy(RuntimeError):
    pass


def run_command(action: str, payload: dict | None = None,
                timeout: float | None = None) -> dict:
    """Write a command, block until its result arrives or timeout."""
    timeout = timeout or CHARTCTL_COMMAND_TIMEOUT_SECONDS
    if not _LOCK.acquire(blocking=False):
        raise LoaderBusy("another command is in flight")
    try:
        paths.ensure_dirs()
        if os.path.exists(paths.COMMAND_PATH):
            # Stale command from a crashed request: clear it.
            _try_remove(paths.COMMAND_PATH)
        _try_remove(paths.COMMAND_RESULT_PATH)

        command_id = "cmd_" + secrets.token_hex(4)
        body = {"command_id": command_id, "action": action}
        body.update(payload or {})
        paths.atomic_write_text(paths.COMMAND_PATH,
                                json.dumps(body, indent=2, sort_keys=True))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = _read_result()
            if result and result.get("command_id") == command_id:
                _try_remove(paths.COMMAND_RESULT_PATH)
                return result
            time.sleep(0.25)
        _try_remove(paths.COMMAND_PATH)
        raise LoaderTimeout(
            f"loader did not answer '{action}' within {timeout:.0f}s")
    finally:
        _LOCK.release()


def _read_result() -> dict | None:
    try:
        with open(paths.COMMAND_RESULT_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _try_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
