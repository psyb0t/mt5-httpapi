import os
import threading
import time

import MetaTrader5 as mt5
from mt5api.config import INI_FILE
from mt5api.logger import log
import mt5api.mt5client as mt5client_module
from mt5api.mt5client import SESSION_ACQUIRE_TIMEOUT, MT5LockTimeout, MT5Timeout, m, session

CHECK_INTERVAL = 60
DEAD_CHECKS_BEFORE_RESTART = 5
# The generation watcher's check is a psutil call on one pid, so it can run
# often: a terminal that restarted on its own gets its WebRequest list back
# about this long plus the GUI settle time after it started.
GENERATION_CHECK_INTERVAL = 10
# A stored WebRequest list missing from the running terminal for longer than
# this is logged as an error on every health pass, for the log alerts.
WEBREQUEST_STALE_ALERT_SECONDS = 300


def _check_ini_autotrading():
    """Return True if mt5start.ini has AutoTrading=1 under [Common]."""
    if not os.path.exists(INI_FILE):
        return False
    try:
        with open(INI_FILE, "r") as f:
            in_common = False
            for line in f:
                stripped = line.strip()
                if stripped.startswith("["):
                    in_common = stripped.lower() == "[common]"
                    continue
                if not in_common:
                    continue
                if stripped.lower().startswith("autotrading="):
                    return stripped.split("=", 1)[1].strip() == "1"
    except OSError:
        return False
    return False


def _webrequest_stale_check(state: dict, now: float) -> None:
    """Log loudly when the running terminal has gone without its stored
    WebRequest list for WEBREQUEST_STALE_ALERT_SECONDS, and once when it is back.

    Uses the generation the watcher last saw, so it never runs a pid lookup."""
    from mt5api.chartctl import terminal_pid
    from mt5api.chartctl import webrequest_apply as wa

    if not wa.available():
        return
    generation = terminal_pid.last_generation()
    if not wa.is_stale(generation):
        if state.get("alerted"):
            log.info("WebRequest list restored on the terminal (pid=%s).",
                     generation[0] if generation else None)
        state["since"] = None
        state["alerted"] = False
        return
    if state.get("since") is None:
        state["since"] = now
    stale_for = now - state["since"]
    if stale_for > WEBREQUEST_STALE_ALERT_SECONDS:
        last = wa.load_status() or {}
        log.error(
            "!!! WEBREQUEST LIST NOT RESTORED !!! (pid=%d, stale %ds, last apply: %s/%s)",
            generation[0], int(stale_for), last.get("status"), last.get("code"),
        )
        state["alerted"] = True


def _generation_step(state: dict) -> None:
    """One check of the generation watcher: when a new terminal64.exe is
    running, reconnect the SDK to it and re-apply the WebRequest list.

    The terminal may have restarted on its own (LiveUpdate), been started by
    the SDK (ensure_initialized from a handler) or by hand after a crash; none
    of those go through restart_terminal, which re-applies by itself. The SDK
    is reconnected first because after such a restart it can stay attached to
    the dead process: terminal_info() keeps failing and the health monitor
    would kill the freshly started terminal after its dead checks."""
    from mt5api import mt5client
    from mt5api.backtest.handler import RUN_LOCK
    from mt5api.chartctl import terminal_pid
    from mt5api.chartctl import webrequest_apply as wa

    generation = terminal_pid.current_generation()
    if generation is None or generation == state.get("last_seen"):
        return
    if RUN_LOCK.locked():
        return  # the tester's process runs from the same exe; look again after the run
    if mt5client.RESTART_IN_PROGRESS.is_set():
        return  # restart_terminal reconnects and re-applies itself; record nothing yet
    previous = state.get("last_seen")
    state["last_seen"] = generation
    if previous is None:
        # The first sighting after the API started: the start-up path connects
        # and re-applies by itself (main._reapply_webrequest_once).
        log.info("Terminal process: pid=%d", generation[0])
        return
    log.warning("Terminal process changed: pid=%d (was %d)", generation[0], previous[0])
    try:
        with session(bypass_queue_cap=True):
            reconnected = mt5client.ensure_initialized()
    except MT5LockTimeout:
        log.warning("SDK reconnect after a terminal process change waited too long for the lock")
        reconnected = False
    except Exception:  # noqa: BLE001 - the re-apply below drives the window, not the SDK
        log.exception("SDK reconnect after a terminal process change failed")
        reconnected = False
    if not reconnected:
        log.warning("SDK did not reconnect to the new terminal process; "
                    "the health monitor's dead checks take over")
    if wa.is_stale(generation) and not wa.reapply_pending():
        wa.reapply_in_background(wa.gui_settle_seconds(), "new terminal process")


def _generation_loop():
    state: dict = {"last_seen": None}
    while True:
        time.sleep(GENERATION_CHECK_INTERVAL)
        try:
            _generation_step(state)
        except Exception:  # noqa: BLE001 - never kill the watcher thread
            log.exception("Terminal generation check failed")


def _monitor_loop():
    from mt5api.mt5client import _kill_terminal, restart_terminal

    prev_logged_in = None
    prev_trade_allowed = None
    dead_count = 0
    stale_state: dict = {}

    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            _webrequest_stale_check(stale_state, time.monotonic())
        except Exception:  # noqa: BLE001 - an optional alert must not stop the health check
            log.exception("WebRequest stale check failed")

        info = None
        acc = None
        # Hold the lock for the whole check pass so terminal_info /
        # account_info are taken from a coherent SDK state, and so a
        # restart triggered below can run inside the same session. The
        # queue-depth cap is skipped: client backpressure must not stop the
        # check that recovers from it.
        try:
            with session(bypass_queue_cap=True):
                try:
                    info = m(mt5.terminal_info, _timeout=15)
                    if info is not None:
                        # Cosmetic cache for lock-free readers (POST /webrequest/verify's build).
                        mt5client_module.LAST_TERMINAL_INFO = info
                except MT5Timeout:
                    info = None
                except Exception:
                    info = None

                if info is None:
                    dead_count += 1
                    log.error(
                        "!!! TERMINAL NOT RUNNING !!! (%d/%d before restart)",
                        dead_count,
                        DEAD_CHECKS_BEFORE_RESTART,
                    )
                    prev_logged_in = None
                    prev_trade_allowed = None

                    if dead_count >= DEAD_CHECKS_BEFORE_RESTART:
                        log.error("Terminal dead for %d checks, restarting...", dead_count)
                        dead_count = 0
                        if restart_terminal():
                            log.info("Auto-restart succeeded.")
                        else:
                            log.error("Auto-restart FAILED, will keep trying.")
                    continue

                dead_count = 0

                trade_allowed = bool(info.trade_allowed)
                if not trade_allowed:
                    log.error("!!! ALGO TRADING DISABLED IN TERMINAL !!!")
                    if not _check_ini_autotrading():
                        log.error("!!! INI FILE MISSING AutoTrading=1 — config is broken !!!")
                elif prev_trade_allowed is not True:
                    log.info("Algo trading is enabled.")
                prev_trade_allowed = trade_allowed

                try:
                    acc = m(mt5.account_info, _timeout=15)
                except MT5Timeout:
                    acc = None
                except Exception:
                    acc = None
        except MT5LockTimeout:
            # Something has held the MT5 lock past SESSION_ACQUIRE_TIMEOUT, so
            # the terminal is not answering it. Count it like a dead check.
            # restart_terminal needs the lock, so at the threshold only kill
            # the terminal: that makes the stuck SDK call return and frees the
            # lock, and the next failed check restarts the terminal under it.
            dead_count += 1
            log.error(
                "!!! MT5 LOCK HELD FOR OVER %ds !!! (%d/%d before restart)",
                SESSION_ACQUIRE_TIMEOUT,
                dead_count,
                DEAD_CHECKS_BEFORE_RESTART,
            )
            prev_logged_in = None
            prev_trade_allowed = None
            if dead_count >= DEAD_CHECKS_BEFORE_RESTART:
                log.error(
                    "MT5 lock unobtainable for %d checks, killing the terminal to release it...",
                    dead_count,
                )
                if _kill_terminal():
                    log.info("Terminal killed; the next check restarts it.")
                else:
                    log.error("No terminal process found to kill.")
                dead_count = DEAD_CHECKS_BEFORE_RESTART - 1
            continue
        except Exception as e:  # any other failure: log and retry, never kill the monitor thread
            log.warning("Monitor session failed: %s", e)
            continue

        if acc is None or acc.login == 0:
            log.error("!!! NOT LOGGED IN !!!")
            prev_logged_in = False
            continue

        if prev_logged_in is not True:
            log.info("Logged in as %s on %s", acc.login, acc.server)
        prev_logged_in = True


def start_monitor():
    """Start the background health monitor daemon thread, and in the VM the
    terminal generation watcher beside it."""
    from mt5api.chartctl import webrequest_apply as wa

    t = threading.Thread(target=_monitor_loop, daemon=True, name="mt5-monitor")
    t.start()
    log.info("Health monitor started (check every %ds).", CHECK_INTERVAL)
    if wa.available():
        g = threading.Thread(target=_generation_loop, daemon=True, name="terminal-generation")
        g.start()
        log.info("Terminal generation watcher started (check every %ds).",
                 GENERATION_CHECK_INTERVAL)
