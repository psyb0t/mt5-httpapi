"""What this API process is doing that a scheduled VM reboot would break.

The VM reboots itself every reboot_interval minutes. Before it does,
scripts/reboot_guard.py asks every API on the VM through GET /busy and holds
the reboot while any of them is busy:

- a backtest of this terminal is queued or running, or a cancelled one is
  still stopping its tester processes;
- a request that changes something is in flight: an order, a position
  change such as a new stop loss, a deployment, a file write, a compile, a
  terminal restart. Reads (GET, HEAD, OPTIONS) never hold a reboot.

Once every API is idle the guard creates the drain flag and checks once
more. While the flag is fresh, new writes are refused with 503
REBOOT_PENDING, so none can start in the seconds between that last check and
the reboot. A stale flag (a guard that died, or one left over from before a
boot) is ignored.
"""
from __future__ import annotations

import os
import threading
import time

from mt5api.backtest import handler as backtest_handler
from mt5api.backtest import jobs as backtest_jobs
from mt5api.config import BASE_DIR
from mt5api.logger import log

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
DRAIN_FLAG = os.path.join(BASE_DIR, "reboot.draining")
# Longer than the guard needs between raising the flag and rebooting, short
# enough that a guard which died never blocks writes for long.
DRAIN_FLAG_MAX_AGE_SECONDS = 120
DRAIN_RETRY_AFTER_SECONDS = 120

REASON_BACKTEST = "backtest"
REASON_WRITE = "write_in_flight"

_lock = threading.Lock()
_writes: dict[str, tuple[str, str, float]] = {}


def is_write(method: str) -> bool:
    return method.upper() not in READ_METHODS


def write_started(request_id: str, method: str, path: str) -> None:
    with _lock:
        _writes[request_id] = (method, path, time.monotonic())


def write_finished(request_id: str) -> None:
    with _lock:
        _writes.pop(request_id, None)


def draining() -> bool:
    """Whether a reboot is about to happen and new writes must wait."""
    try:
        age = time.time() - os.path.getmtime(DRAIN_FLAG)
    except FileNotFoundError:
        return False
    except OSError as err:
        log.warning("reboot guard: cannot read the drain flag, treating it as absent: %s", err)
        return False
    return 0 <= age <= DRAIN_FLAG_MAX_AGE_SECONDS


def _writes_in_flight() -> list[dict]:
    now = time.monotonic()
    with _lock:
        snapshot = list(_writes.values())
    return [
        {"method": method, "path": path, "seconds": round(now - started, 1)}
        for method, path, started in snapshot
    ]


def _active_backtests() -> list[dict]:
    # A cancelled job is final at once, but its worker goes on killing the
    # terminal's tester processes; a reboot then would cut that short.
    workers = backtest_handler.active_worker_ids()
    with backtest_jobs.JOB_LOCK:
        snapshot = list(backtest_jobs.BACKTEST_JOBS.values())
    return [
        {"job_id": job.get("jobId"), "status": job.get("status")}
        for job in snapshot
        if (job.get("status") in backtest_jobs.ACTIVE_STATUSES or job.get("jobId") in workers)
        and backtest_jobs.owns_job(job)
    ]


def status() -> dict:
    """The GET /busy body: whether a reboot now would break work, and why."""
    writes = _writes_in_flight()
    backtests = _active_backtests()
    reasons = [
        f"{REASON_BACKTEST} {b['job_id']} {b['status']}" for b in backtests
    ] + [
        f"{REASON_WRITE} {w['method']} {w['path']} ({w['seconds']}s)" for w in writes
    ]
    return {
        "busy": bool(reasons),
        "reasons": reasons,
        "backtests": backtests,
        "writes_in_flight": writes,
        "draining": draining(),
    }
