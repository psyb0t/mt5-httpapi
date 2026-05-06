import os
import threading
import time

import MetaTrader5 as mt5
from mt5api.config import INI_FILE
from mt5api.logger import log
from mt5api.mt5client import MT5Timeout, m, session

CHECK_INTERVAL = 60
DEAD_CHECKS_BEFORE_RESTART = 5


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


def _monitor_loop():
    from mt5api.mt5client import restart_terminal

    prev_logged_in = None
    prev_trade_allowed = None
    dead_count = 0

    while True:
        time.sleep(CHECK_INTERVAL)

        info = None
        acc = None
        # Hold the lock for the whole check pass so terminal_info /
        # account_info are taken from a coherent SDK state, and so a
        # restart triggered below can run inside the same session.
        try:
            with session():
                try:
                    info = m(mt5.terminal_info, _timeout=15)
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
        except Exception as e:
            # session() backpressure (QueueFull) etc — surface and retry.
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
    """Start the background health monitor daemon thread."""
    t = threading.Thread(target=_monitor_loop, daemon=True, name="mt5-monitor")
    t.start()
    log.info("Health monitor started (check every %ds).", CHECK_INTERVAL)
