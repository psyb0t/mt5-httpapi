import os
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps

import MetaTrader5 as mt5
import psutil
from flask import has_request_context, g, jsonify
from mt5api.config import (
    ACCOUNT,
    BROKER,
    FILLING_MAP,
    INI_FILE,
    ORDER_TYPE_MAP,
    TERMINAL_DIR,
    TERMINAL_PATH,
    TIME_MAP,
    UTC_OFFSET_SECONDS,
    load_yaml_config,
)
from mt5api.logger import log

# Field names whose values are unix-seconds in BROKER time and need to be
# converted to real UTC for the wire response.
TIME_FIELDS_SECONDS = frozenset({
    "time",
    "time_setup",
    "time_done",
    "time_expiration",
    "time_update",
    "time_current",
    "time_last",
})

# Same idea, but milliseconds.
TIME_FIELDS_MS = frozenset({
    "time_msc",
    "time_setup_msc",
    "time_done_msc",
    "time_update_msc",
})


def broker_to_utc_seconds(broker_unix):
    """Convert a broker-time unix-seconds value into real UTC unix-seconds."""
    return int(broker_unix) - UTC_OFFSET_SECONDS


def broker_to_utc_ms(broker_unix_ms):
    """Convert a broker-time unix-milliseconds value into real UTC unix-ms."""
    return int(broker_unix_ms) - UTC_OFFSET_SECONDS * 1000


def utc_seconds_to_broker_dt(utc_unix):
    """Convert a real-UTC unix-seconds input into the broker-time datetime
    that MT5 expects for copy_*_range / copy_*_from calls.

    MT5 reads only the clock face of the supplied datetime — it does NOT
    apply any timezone math — and treats those wall-clock numbers as broker
    server time. So to ask MT5 about a real UTC moment, we need to hand it a
    datetime whose clock face matches the broker's wall clock at that moment.
    Using a tz-aware UTC datetime here is intentional — it just makes the
    clock face deterministic across systems with different LOCAL timezones.
    """
    return datetime.fromtimestamp(int(utc_unix) + UTC_OFFSET_SECONDS, tz=timezone.utc)


INIT_TIMEOUT = 60

# Hard cap on any single MT5 SDK call. The SDK can wedge in C code on a
# stalled terminal pipe and there's no way to interrupt a Python thread
# that's blocked inside a C extension — we just stop waiting and let the
# orphaned worker thread die when the process is restarted by the monitor.
MT5_CALL_TIMEOUT = 30

# Backpressure: if more than this many requests are queued / in-flight on
# the MT5 lock, fast-503 new ones instead of letting them pile up. With 1
# physical worker (the SDK is single-connection, single-process) the queue
# is the *only* thing growing under load — capping it keeps p99 bounded
# and stops nginx/clients from hanging on a stuck terminal.
MAX_QUEUE_DEPTH = int(os.environ.get("MT5_MAX_QUEUE_DEPTH", "20"))

# How long session() will wait for the lock before giving up with 503.
# Picks up wedges that exceed the per-call timeout (e.g. handler hung
# between calls — shouldn't happen, but be defensive).
SESSION_ACQUIRE_TIMEOUT = MT5_CALL_TIMEOUT + 30


class MT5Timeout(Exception):
    """A single mt5.* call exceeded MT5_CALL_TIMEOUT."""


class QueueFull(Exception):
    """Too many requests queued on the MT5 lock — fast-fail to client."""


# Single mutex serializing every mt5.* call across handlers, monitor, and
# background init. The MT5 SDK is one connection per process and is not
# threadsafe — concurrent calls corrupt internal state (notably last_error,
# which is read implicitly by many code paths).
_mt5_lock = threading.Lock()

_queue_depth = 0
_queue_depth_lock = threading.Lock()


def _bump_depth():
    global _queue_depth
    with _queue_depth_lock:
        _queue_depth += 1
        return _queue_depth


def _drop_depth():
    global _queue_depth
    with _queue_depth_lock:
        _queue_depth -= 1


def current_queue_depth():
    with _queue_depth_lock:
        return _queue_depth


def _req_id():
    if has_request_context():
        return getattr(g, "req_id", "-")
    return "-"


def load_accounts():
    cfg = load_yaml_config()
    accounts = cfg.get("accounts") or {}
    return accounts.get(BROKER, {}) or {}


def get_first_account():
    accounts = load_accounts()
    if not accounts:
        return None
    if ACCOUNT and ACCOUNT in accounts:
        return accounts[ACCOUNT]
    return next(iter(accounts.values()))


def _run_with_timeout(fn, timeout=INIT_TIMEOUT):
    """Run fn() in a thread with a timeout.

    Returns fn's result. Raises MT5Timeout if fn didn't complete in time.
    Re-raises any exception fn raised. Distinguishing timeout from a None
    return is critical — many mt5.* calls return None as a legitimate
    "no data" answer.
    """
    box: list = [None]
    err_box: list = [None]
    done = threading.Event()

    def _worker():
        try:
            box[0] = fn()
        except BaseException as e:
            err_box[0] = e
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not done.wait(timeout=timeout):
        log.warning("MT5 call timed out after %ds", timeout)
        raise MT5Timeout(f"call timed out after {timeout}s")
    if err_box[0] is not None:
        raise err_box[0]
    return box[0]


def m(fn, *args, _timeout=MT5_CALL_TIMEOUT, **kwargs):
    """Call an mt5.* function with a hard timeout and per-call timing log.

    Caller must already hold the MT5 lock (via session() or @with_mt5()).
    On wedge: raises MT5Timeout — handler returns 504, lock is released,
    monitor will eventually detect and restart the terminal.
    """
    name = getattr(fn, "__name__", "?")
    rid = _req_id()
    t0 = time.monotonic()
    timed_out = False
    try:
        return _run_with_timeout(lambda: fn(*args, **kwargs), timeout=_timeout)
    except MT5Timeout:
        timed_out = True
        raise
    finally:
        dur = (time.monotonic() - t0) * 1000
        if timed_out:
            log.error("%s mt5.%s TIMEOUT after %.1fms", rid, name, dur)
        else:
            log.info("%s mt5.%s dur_ms=%.1f", rid, name, dur)


@contextmanager
def session():
    """Acquire the MT5 lock for the duration of the block.

    Bumps queue depth on entry; fast-fails with QueueFull if too many
    requests are already piled up. Releases lock + decrements depth on
    exit, regardless of how the block exits.
    """
    depth = _bump_depth()
    try:
        if depth > MAX_QUEUE_DEPTH:
            log.warning(
                "%s queue depth %d exceeds max %d — rejecting",
                _req_id(), depth, MAX_QUEUE_DEPTH,
            )
            raise QueueFull(f"queue depth {depth} exceeds max {MAX_QUEUE_DEPTH}")
        log.info("%s session acquire (depth=%d)", _req_id(), depth)
        if not _mt5_lock.acquire(timeout=SESSION_ACQUIRE_TIMEOUT):
            log.error(
                "%s session lock acquire timeout after %ds",
                _req_id(), SESSION_ACQUIRE_TIMEOUT,
            )
            raise QueueFull("could not acquire MT5 lock")
        try:
            yield
        finally:
            _mt5_lock.release()
    finally:
        _drop_depth()


def with_mt5(handler):
    """Decorator for Flask handlers that touch MT5.

    Holds the MT5 lock for the entire handler body so multi-call handlers
    (order placement, get_rates retry loops) are atomic vs. other handlers.
    Maps backpressure / wedge exceptions to HTTP error responses.
    """
    @wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            with session():
                return handler(*args, **kwargs)
        except QueueFull as e:
            return jsonify({"error": str(e)}), 503
        except MT5Timeout as e:
            return jsonify({"error": f"mt5 call timed out: {e}"}), 504
    return wrapper


def ensure_symbol(symbol):
    """Make sure symbol is selected in MarketWatch. Returns True if known."""
    info = m(mt5.symbol_info, symbol)
    if info is None:
        return False
    if not info.visible:
        m(mt5.symbol_select, symbol, True)
    return True


def init_mt5(login=None, password=None, server=None):
    """Initialize the MT5 connection.

    Caller must hold the MT5 lock (via session() or be in a @with_mt5()
    handler). Returns True/False based on SDK result. Returns False on
    timeout rather than raising, since callers (startup, monitor,
    ensure_initialized) all want to retry rather than surface to clients.
    """
    kwargs = {"path": TERMINAL_PATH}
    if login:
        kwargs["login"] = int(login)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server
    log.info("Initializing MT5 (login=%s, server=%s)...", login, server)
    try:
        result = m(mt5.initialize, _timeout=INIT_TIMEOUT, **kwargs)
    except MT5Timeout:
        log.error("MT5 initialization timed out.")
        return False
    if result:
        log.info("MT5 initialized successfully.")
    else:
        log.error("MT5 initialization failed.")
    return result


def ensure_initialized():
    """Probe + reconnect helper. Caller must hold the MT5 lock."""
    try:
        info = m(mt5.terminal_info, _timeout=15)
    except MT5Timeout:
        info = None
    if info is None:
        log.warning("Terminal not responding, attempting full init...")
        account = get_first_account()
        if account:
            return init_mt5(**account)
        return init_mt5()

    # Terminal is running — check if actually logged in
    try:
        acc = m(mt5.account_info, _timeout=15)
    except MT5Timeout:
        acc = None
    if acc is not None and acc.login != 0:
        return True

    # Not logged in — try mt5.login()
    log.warning("Not logged in, attempting login...")
    account = get_first_account()
    if not account:
        return True
    try:
        return m(
            mt5.login,
            login=int(account["login"]),
            password=account["password"],
            server=account["server"],
            _timeout=INIT_TIMEOUT,
        )
    except MT5Timeout:
        return False


def _kill_terminal():
    """Kill the terminal64.exe in our terminal directory."""
    # WMI via PowerShell — can see and kill elevated processes
    # (psutil can't read exe paths of elevated processes from non-elevated context)
    path_filter = f"*\\{BROKER}\\{ACCOUNT}\\*" if ACCOUNT else f"*\\{BROKER}\\*"
    ps_cmd = (
        "$p = Get-WmiObject Win32_Process -Filter \"Name='terminal64.exe'\" "
        "| Where-Object { $_.ExecutablePath -like '" + path_filter + "' }; "
        "if ($p) { $p | ForEach-Object { $_.Terminate() }; 'KILLED' } "
        "else { 'NONE' }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if "KILLED" in result.stdout:
            log.info("Terminal process killed.")
            time.sleep(2)
            return True
        if "NONE" in result.stdout:
            return False
    except Exception as e:
        log.warning("WMI kill failed (%s), trying psutil...", e)

    # Fallback: psutil (match by name + directory, not exact path)
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = proc.info.get("name", "")
            if not name or name.lower() != "terminal64.exe":
                continue
            exe = proc.info.get("exe") or ""
            if TERMINAL_DIR.lower() not in exe.lower():
                continue
            log.info("Killing terminal PID %d via psutil", proc.pid)
            proc.kill()
            proc.wait(timeout=10)
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            continue
    return False


def _wait_for_journal(journal_log, offset, max_attempts=60):
    """Poll journal log for 'started for'. 5s between attempts."""
    for i in range(max_attempts):
        time.sleep(5)
        if not os.path.exists(journal_log):
            log.info("Waiting for terminal to start (%d)...", i + 1)
            continue
        try:
            with open(journal_log, "rb") as f:
                f.seek(offset)
                data = f.read().decode("utf-16-le", errors="ignore")
            if "started for" in data:
                return True
        except OSError:
            pass
        log.info("Waiting for terminal to start (%d)...", i + 1)
    return False


def restart_terminal():
    """Kill terminal, relaunch, wait for journal 'started for', reconnect.

    Caller must hold the MT5 lock — this blocks the lock for ~minutes
    while waiting for the terminal to come back up, which is intentional:
    handler requests during a restart get fast-503'd via the queue-depth
    backpressure rather than piling up against a dead terminal.
    """
    log.info("Restarting terminal...")
    try:
        m(mt5.shutdown, _timeout=15)
    except MT5Timeout:
        log.warning("mt5.shutdown timed out — proceeding to kill anyway.")

    killed = _kill_terminal()
    if not killed:
        log.warning("No terminal process found, launching fresh.")

    today = date.today().strftime("%Y%m%d")
    journal_log = os.path.join(TERMINAL_DIR, "logs", f"{today}.log")
    offset = 0
    if os.path.exists(journal_log):
        offset = os.path.getsize(journal_log)

    log.info("Launching terminal: %s", TERMINAL_PATH)
    ps_launch = (
        f"Start-Process '{TERMINAL_PATH}' "
        f"-ArgumentList '/portable','/config:\"{INI_FILE}\"' "
        "-Verb RunAs -WindowStyle Normal"
    )
    subprocess.Popen(["powershell", "-Command", ps_launch])

    if not _wait_for_journal(journal_log, offset):
        log.error("Terminal failed to start after 5 minutes!")
        return False

    log.info("Terminal started, reconnecting...")
    account = get_first_account()
    if account:
        result = init_mt5(**account)
    else:
        result = init_mt5()

    if not result:
        log.error("Terminal restarted but reconnection failed.")
        return False

    log.info("Terminal restarted successfully.")
    return True


def to_dict(named_tuple):
    if named_tuple is None:
        return None
    d = named_tuple._asdict()
    if UTC_OFFSET_SECONDS == 0:
        return d
    for k in TIME_FIELDS_SECONDS:
        v = d.get(k)
        if v:
            d[k] = int(v) - UTC_OFFSET_SECONDS
    for k in TIME_FIELDS_MS:
        v = d.get(k)
        if v:
            d[k] = int(v) - UTC_OFFSET_SECONDS * 1000
    return d


def build_order_request(body):
    """Build an MT5 order request dict from a JSON body. Returns (request, error)."""
    request = {}

    if "action" in body:
        action_map = {
            "DEAL": mt5.TRADE_ACTION_DEAL,
            "PENDING": mt5.TRADE_ACTION_PENDING,
            "SLTP": mt5.TRADE_ACTION_SLTP,
            "MODIFY": mt5.TRADE_ACTION_MODIFY,
            "REMOVE": mt5.TRADE_ACTION_REMOVE,
            "CLOSE_BY": mt5.TRADE_ACTION_CLOSE_BY,
        }
        a = body["action"].upper()
        if a not in action_map:
            return None, f"Invalid action: {a}. Use: {list(action_map.keys())}"
        request["action"] = action_map[a]

    if "symbol" in body:
        request["symbol"] = body["symbol"]
    if "volume" in body:
        request["volume"] = float(body["volume"])
    if "price" in body:
        request["price"] = float(body["price"])
    if "sl" in body:
        request["sl"] = float(body["sl"])
    if "tp" in body:
        request["tp"] = float(body["tp"])
    if "deviation" in body:
        request["deviation"] = int(body["deviation"])
    if "magic" in body:
        request["magic"] = int(body["magic"])
    if "comment" in body:
        request["comment"] = body["comment"]
    if "position" in body:
        request["position"] = int(body["position"])
    if "position_by" in body:
        request["position_by"] = int(body["position_by"])

    if "type" in body:
        t = body["type"].upper()
        if t not in ORDER_TYPE_MAP:
            return None, f"Invalid type: {t}. Use: {list(ORDER_TYPE_MAP.keys())}"
        request["type"] = ORDER_TYPE_MAP[t]

    if "type_filling" in body:
        f = body["type_filling"].upper()
        if f in FILLING_MAP:
            request["type_filling"] = FILLING_MAP[f]
    elif "type_filling" not in request:
        request["type_filling"] = mt5.ORDER_FILLING_IOC

    if "type_time" in body:
        tt = body["type_time"].upper()
        if tt in TIME_MAP:
            request["type_time"] = TIME_MAP[tt]

    return request, None
