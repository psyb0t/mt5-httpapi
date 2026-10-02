"""Wedge watchdog — exits the API process if an SDK call stays stuck too long.

See docs/spec/mt5-httpapi-sdk-call-thread-leak.md. The single-flight guard in
mt5client (MT5Wedged) stops a stuck SDK call from taking down the rest of the
process via saturation, but it does not make the stuck call go away — every
@with_mt5 route on this process keeps 503'ing until either the zombie thread
returns on its own or something restarts the process. In live mode the health
monitor (monitor.py) eventually calls restart_terminal(), but backtest-mode
processes never start the monitor (main.py, `if MODE == "backtest"`), so a
wedge there would otherwise persist until someone notices — this thread is
the one thing that runs in both modes and can act on a wedge outright.

Runs on its own daemon thread, never taking `_mt5_lock`: only reads the
lock-free SDK-worker snapshot mt5client already tracks for /ping.
"""
import os
import threading
import time

from mt5api.config import (
    MODE,
    MT5_WEDGE_CHECK_INTERVAL_SECONDS,
    MT5_WEDGE_DEFER_CEILING_SECONDS,
    MT5_WEDGE_EXIT_SECONDS,
)
from mt5api.logger import log
from mt5api.mt5client import sdk_wedge_status

# No cleanup handlers, no atexit, no finally blocks up the stack — a process
# that is willing to exit because an SDK call has been stuck for minutes
# cannot be trusted to shut down gracefully (the SDK's own internal state may
# already be corrupted by the concurrent-call issue this whole spec is
# about). The relaunch loop this spec adds to api_runner.bat is what brings a
# clean process back up after this exit.
WEDGE_EXIT_CODE = 75


def _default_exit(code):
    os._exit(code)


def _run_lock_held():
    """True if a backtest job currently holds RUN_LOCK.

    Backtest-only: imported lazily, same reasoning as main.py's own lazy
    import of mt5api.backtest.handler — that module is meaningless outside
    backtest mode and this function is never called outside it either.
    """
    from mt5api.backtest.handler import RUN_LOCK

    return RUN_LOCK.locked()


def _watchdog_loop(exit_fn, run_lock_held, sleep_fn):
    # Wall-clock instant (time.monotonic()) the watchdog first wanted to
    # exit, or None. Deliberately NOT reset by RUN_LOCK being briefly free
    # between two queued jobs — see MT5_WEDGE_DEFER_CEILING_SECONDS in
    # config.py: jobs here chain for up to ~6h, so resetting on every lock
    # handoff would make the ceiling meaningless.
    wants_to_exit_since = None

    while True:
        sleep_fn(MT5_WEDGE_CHECK_INTERVAL_SECONDS)

        status = sdk_wedge_status()
        if status is None or status[1] < MT5_WEDGE_EXIT_SECONDS:
            wants_to_exit_since = None
            continue
        fn_name, age = status

        if wants_to_exit_since is None:
            wants_to_exit_since = time.monotonic()

        if MODE == "backtest" and run_lock_held():
            deferred_for = time.monotonic() - wants_to_exit_since
            if deferred_for < MT5_WEDGE_DEFER_CEILING_SECONDS:
                log.error(
                    "Wedge watchdog: %s stuck %.0fs — deferring exit, RUN_LOCK held "
                    "(deferred %.0fs of %ds ceiling)",
                    fn_name, age, deferred_for, MT5_WEDGE_DEFER_CEILING_SECONDS,
                )
                continue
            log.error(
                "Wedge watchdog: %s stuck %.0fs — RUN_LOCK defer ceiling (%ds) reached, "
                "exiting anyway.",
                fn_name, age, MT5_WEDGE_DEFER_CEILING_SECONDS,
            )
        else:
            log.error(
                "Wedge watchdog: %s stuck %.0fs (> %ds) — exiting process.",
                fn_name, age, MT5_WEDGE_EXIT_SECONDS,
            )

        exit_fn(WEDGE_EXIT_CODE)
        return


def start_wedge_watchdog(exit_fn=_default_exit, run_lock_held=_run_lock_held, sleep_fn=time.sleep):
    """Start the watchdog daemon thread. Args are overridable for tests."""
    t = threading.Thread(
        target=_watchdog_loop,
        args=(exit_fn, run_lock_held, sleep_fn),
        daemon=True,
        name="mt5-wedge-watchdog",
    )
    t.start()
    log.info(
        "Wedge watchdog started (exit after %ds stuck, check every %ds, mode=%s).",
        MT5_WEDGE_EXIT_SECONDS, MT5_WEDGE_CHECK_INTERVAL_SECONDS, MODE,
    )
    return t
