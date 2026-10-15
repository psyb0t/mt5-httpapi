"""Apply the WebRequest allowlist to the running terminal in the VM.

A terminal started with ``/config:<file>`` (every terminal in the VM) never
writes its settings back, so it forgets the list each time terminal64.exe
starts. The API therefore sets the list the way a user would, in the
terminal's own Options dialog, with window messages (winoptions), and
re-applies it after the API starts and after every terminal restart it
performs. A bare-metal terminal, where common.ini is the store, keeps the
common.ini rewrite plus restart in handlers/webrequest.py.

Every apply records its outcome in ``Config/webrequest_status.json``, which
``GET /webrequest`` returns as ``last_apply``: the background re-apply gives
up after a few attempts and would otherwise only leave a log line.

No GUI call runs while a backtest holds RUN_LOCK: the tester's process runs
from the same terminal64.exe, so the pid lookup would find it.

Each successful apply records the terminal process it applied to (its
"generation", terminal_pid.py), so the generation watcher in monitor.py can
tell a terminal that restarted on its own (LiveUpdate, a crash, an SDK-started
terminal) and re-apply there too.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

from mt5api.chartctl import terminal_pid, winoptions
from mt5api.chartctl import webrequest as wr
from mt5api.chartctl.terminal_pid import TerminalPidError
from mt5api.chartctl.winoptions import OptionsError
from mt5api.config import PORT, TERMINAL_DIR
from mt5api.logger import log

STATUS_OK = "OK"
STATUS_NAME = "webrequest_status.json"
CODE_BACKTEST_RUNNING = "backtest_running"
CODE_INTERNAL = "internal_error"
REASON_VERIFY = "verify"

_REAPPLY_ATTEMPTS = 3
_REAPPLY_RETRY_SECONDS = 15
# How often a re-apply blocked by a running backtest looks again. A wait is
# not one of its attempts: a backtest can run for hours.
_BACKTEST_POLL_SECONDS = 30
# Seconds to let a freshly started terminal's GUI settle before driving it,
# plus a per-port stagger so one VM's terminals do not all open their Options
# dialogs in the same second at boot.
_GUI_SETTLE_SECONDS = 25
_GUI_SETTLE_STAGGER_SECONDS = 3
_GUI_SETTLE_STAGGER_SLOTS = 10


def available() -> bool:
    """True on Windows (the VM), where the terminal's windows can be driven.
    Elsewhere the caller uses the common.ini fallback."""
    return os.name == "nt"


def backtest_running() -> bool:
    # Imported here: the backtest handler pulls in Flask views this module
    # does not otherwise need.
    from mt5api.backtest.handler import RUN_LOCK

    return RUN_LOCK.locked()


def gui_settle_seconds() -> int:
    """How long to wait after a terminal starts before driving its GUI."""
    stagger = (PORT % _GUI_SETTLE_STAGGER_SLOTS) * _GUI_SETTLE_STAGGER_SECONDS
    return _GUI_SETTLE_SECONDS + stagger


def _cfg_dir() -> str:
    return wr.config_dir(TERMINAL_DIR)


def status_path() -> str:
    return os.path.join(_cfg_dir(), STATUS_NAME)


def load_status() -> dict | None:
    try:
        with open(status_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# Held around "read or save the stored list, then apply it": a PUT and a
# background re-apply would otherwise interleave, and a re-apply that read the
# old list before a patch saved the new one would undo the patch on the
# terminal and still record it as applied. Re-entrant because the handlers
# take it and then call apply_urls, which a re-apply also calls.
APPLY_LOCK = threading.RLock()

# The terminal process the last successful apply went to, as (pid,
# create_time); falls back to the status file after the API process restarts.
_applied_generation: tuple[int, float] | None = None


def applied_generation() -> tuple[int, float] | None:
    if _applied_generation is not None:
        return _applied_generation
    status = load_status() or {}
    if status.get("status") == STATUS_OK and status.get("pid"):
        return int(status["pid"]), float(status.get("pid_started_at") or 0.0)
    return None


def is_stale(generation: tuple[int, float] | None) -> bool:
    """The running terminal has a stored list it has not been given: a
    generation other than the one the last successful apply went to."""
    if generation is None or not wr.effective_urls(_cfg_dir()):
        return False
    return generation != applied_generation()


def _write_status(
    reason: str,
    status: str,
    code: str | None,
    rejected: list[str],
    generation: tuple[int, float] | None = None,
) -> None:
    record = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": reason,
        "status": STATUS_OK if status == STATUS_OK else "FAIL",
        "code": code,
        "rejected": rejected,
        "pid": generation[0] if generation else None,
        "pid_started_at": generation[1] if generation else None,
    }
    path = status_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=STATUS_NAME, suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("WebRequest: could not write %s (%s)", path, exc)


def _drive(
    add: list[str], remove: list[str], exact: list[str] | None, partial: bool
) -> tuple[dict, tuple[int, float]]:
    if backtest_running():
        raise OptionsError(
            CODE_BACKTEST_RUNNING, "a backtest is running on this terminal; try again after it"
        )
    generation = terminal_pid.find_terminal()
    return winoptions.apply(generation[0], add, remove, exact=exact, partial=partial), generation


def _run(
    reason: str, add: list[str], remove: list[str], exact: list[str] | None, partial: bool
) -> tuple[str, dict]:
    """(status, detail): status "OK" or "FAIL:<code>"; detail is the driver's
    result on OK, else {"error", "diag"}. Never raises."""
    global _applied_generation
    try:
        result, generation = _drive(add, remove, exact, partial)
    except (OptionsError, TerminalPidError) as err:
        log.warning(
            "WebRequest apply (%s) failed: %s %s diag=%s",
            reason,
            err.code,
            err.message,
            json.dumps(err.diag)[:800],
        )
        if reason != REASON_VERIFY:  # a failed read says nothing about the last apply
            _write_status(reason, "FAIL", err.code, [])
        return f"FAIL:{err.code}", {"error": err.message, "diag": err.diag}
    except Exception:  # noqa: BLE001 - a ctypes or OS failure must answer, not kill the caller
        log.exception("WebRequest apply (%s) failed unexpectedly", reason)
        if reason != REASON_VERIFY:
            _write_status(reason, "FAIL", CODE_INTERNAL, [])
        return f"FAIL:{CODE_INTERNAL}", {
            "error": "the apply failed unexpectedly; see the API log",
            "diag": {},
        }
    rejected = result.get("rejected", [])
    log.info(
        "WebRequest apply (%s): OK enabled=%s urls=%d added=%d removed=%d rejected=%d",
        reason,
        result.get("enabled"),
        len(result.get("urls", [])),
        len(result.get("added", [])),
        len(result.get("removed", [])),
        len(rejected),
    )
    if reason == REASON_VERIFY:
        return STATUS_OK, result  # a read says nothing about the list being applied
    _applied_generation = generation
    _write_status(reason, STATUS_OK, None, rejected, generation)
    return STATUS_OK, result


def apply_urls(urls: list[str], *, partial: bool = False, reason: str = "put") -> tuple[str, str]:
    """Make the terminal's list exactly ``urls``. Returns (status, json detail)."""
    status, detail = _run(reason, [], [], list(urls), partial)
    return status, json.dumps(detail)


def patch_urls(add: list[str], remove: list[str]) -> tuple[str, str, list[str] | None]:
    """Add and remove only the named URLs, keeping every other row. Returns
    (status, json detail, the URL rows read back on OK else None)."""
    status, detail = _run("patch", list(add), list(remove), None, False)
    return status, json.dumps(detail), (detail.get("urls") if status == STATUS_OK else None)


def read_live() -> tuple[str, dict]:
    """Read the live list without changing it.

    Returns (status, {"enabled", "urls"}) on OK, else (status, {"error", "diag"})."""
    status, detail = _run(REASON_VERIFY, [], [], None, False)
    if status != STATUS_OK:
        return status, detail
    return status, {"enabled": detail.get("enabled"), "urls": detail.get("urls", [])}


# One re-apply thread per API process. API start, a restart and the
# generation watcher can all ask at once; a request while one is waiting or
# running only asks for one more pass after it.
_reapply_lock = threading.Lock()
_reapply_running = False
_reapply_again: tuple[float, str] | None = None


def reapply_pending() -> bool:
    """Whether a re-apply thread is waiting out its delay or applying."""
    with _reapply_lock:
        return _reapply_running


def _reapply_once(delay_seconds: float, reason: str) -> None:
    time.sleep(delay_seconds)
    attempt = 0
    while attempt < _REAPPLY_ATTEMPTS:
        if backtest_running():
            time.sleep(_BACKTEST_POLL_SECONDS)
            continue
        # Read when applying, not when asked: the list may have changed while
        # this pass waited, and the pid is looked up then too (_drive).
        with APPLY_LOCK:
            urls = wr.effective_urls(_cfg_dir())
            if not urls:
                return
            attempt += 1
            status, _detail = apply_urls(urls, partial=True, reason=reason)
        log.info(
            "WebRequest re-apply (%s) attempt %d: %d url(s) -> %s",
            reason,
            attempt,
            len(urls),
            status,
        )
        if status == STATUS_OK:
            return
        time.sleep(_REAPPLY_RETRY_SECONDS)
    log.warning(
        "WebRequest re-apply (%s) gave up after %d attempts; POST /webrequest/apply retries it",
        reason,
        _REAPPLY_ATTEMPTS,
    )


def _reapply_loop(delay_seconds: float, reason: str) -> None:
    """Run passes until no further one was asked for. The running flag is
    cleared only inside the same locked block that finds nothing more to do,
    so a request can never land between the two and be lost or doubled."""
    global _reapply_running, _reapply_again
    while True:
        try:
            _reapply_once(delay_seconds, reason)
        except Exception:  # noqa: BLE001 - a daemon thread must log, not die silently
            log.exception("WebRequest re-apply (%s) failed", reason)
        with _reapply_lock:
            if _reapply_again is None:
                _reapply_running = False
                return
            (delay_seconds, reason), _reapply_again = _reapply_again, None


def reapply_in_background(delay_seconds: float, reason: str) -> None:
    """Re-apply the stored allowlist on a daemon thread, after `delay_seconds`
    for the terminal's GUI to settle.

    Partial: a stored URL MT5 will not take is skipped and reported, so one bad
    entry does not leave the terminal with no allowlist at all. No-op off
    Windows (bare metal keeps the list in common.ini) or without a stored list.
    """
    global _reapply_running, _reapply_again
    if not available() or not wr.effective_urls(_cfg_dir()):
        return
    with _reapply_lock:
        if _reapply_running:
            _reapply_again = (delay_seconds, reason)
            return
        _reapply_running = True
    threading.Thread(
        target=_reapply_loop, args=(delay_seconds, reason), name="webrequest-reapply", daemon=True
    ).start()
