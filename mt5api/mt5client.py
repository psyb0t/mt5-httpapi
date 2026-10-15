import os
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps

import MetaTrader5 as mt5
import psutil
from flask import has_request_context, g, jsonify, request
from mt5api import terminal_mcp
from mt5api.config import (
    ACCOUNT,
    BROKER,
    DISABLE_TERMINAL_MCP,
    FILLING_MAP,
    INI_FILE,
    MODE,
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

# Retry-After on the single-flight guard's 503. Tied to MT5_CALL_TIMEOUT
# because a stuck call can clear once its own timeout window has passed.
MT5_WEDGE_RETRY_AFTER_SECONDS = MT5_CALL_TIMEOUT


class MT5Timeout(Exception):
    """A single mt5.* call exceeded MT5_CALL_TIMEOUT."""


class MT5Wedged(MT5Timeout):
    """An earlier timed-out call is still running inside the SDK.

    Subclasses MT5Timeout so every existing `except MT5Timeout` still treats
    it as an unresponsive terminal. Callers that care catch it first.
    """


class QueueFull(Exception):
    """Too many requests queued on the MT5 lock — fast-fail to client."""


class MT5LockTimeout(QueueFull):
    """The MT5 lock stayed held for longer than SESSION_ACQUIRE_TIMEOUT.

    Subclasses QueueFull so request handlers keep answering 503. The health
    monitor catches it first, because a lock nobody releases means the
    terminal is not answering.
    """


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


class _SdkWorker:
    """A _run_with_timeout call whose thread has not returned yet."""

    __slots__ = ("fn_name", "started_at")

    def __init__(self, fn_name):
        self.fn_name = fn_name
        self.started_at = time.monotonic()

    def age(self):
        return time.monotonic() - self.started_at


# Live SDK worker threads, including ones abandoned after a timeout. Has its
# own lock so /ping can read it without taking _mt5_lock.
_sdk_workers: dict = {}
_sdk_workers_lock = threading.Lock()


def _add_sdk_worker(fn_name):
    key = object()
    with _sdk_workers_lock:
        _sdk_workers[key] = _SdkWorker(fn_name)
    return key


def _remove_sdk_worker(key):
    with _sdk_workers_lock:
        _sdk_workers.pop(key, None)


def _forget_sdk_workers():
    """Drop every tracked worker and return how many there were. A dropped
    thread that returns later removes nothing, since its key is gone."""
    with _sdk_workers_lock:
        count = len(_sdk_workers)
        _sdk_workers.clear()
        return count


def _sdk_worker_state():
    """(alive_count, oldest_worker_or_None), read under one lock."""
    with _sdk_workers_lock:
        oldest = min(_sdk_workers.values(), key=lambda w: w.started_at, default=None)
        return len(_sdk_workers), oldest


def sdk_worker_snapshot():
    """(alive_count, oldest_age_seconds_or_None), for /ping and log lines."""
    count, oldest = _sdk_worker_state()
    return count, oldest.age() if oldest else None


def _req_id():
    if has_request_context():
        return getattr(g, "req_id", "-")
    return "-"


def _req_path():
    """The request path, or "-" outside a request (startup init, monitor)."""
    if has_request_context():
        return request.path
    return "-"


def load_accounts():
    cfg = load_yaml_config()
    accounts = cfg.get("accounts") or {}
    return accounts.get(BROKER, {}) or {}


# The account fields init_mt5() accepts. A config account may carry more, such
# as symbol_map, which init_mt5(**account) would reject with a TypeError.
_LOGIN_FIELDS = ("login", "password", "server")


def get_first_account():
    accounts = load_accounts()
    if not accounts:
        return None
    if ACCOUNT and ACCOUNT in accounts:
        account = accounts[ACCOUNT]
    else:
        account = next(iter(accounts.values()))
    return {k: account[k] for k in _LOGIN_FIELDS if k in account}


def _run_with_timeout(fn, timeout=INIT_TIMEOUT, name="?", allow_wedged=False):
    """Run fn() in a thread with a timeout.

    Returns fn's result. Raises MT5Timeout if fn didn't complete in time.
    Re-raises any exception fn raised. Distinguishing timeout from a None
    return is critical — many mt5.* calls return None as a legitimate
    "no data" answer.

    Single-flight guard: while an earlier call's thread is still inside the
    SDK, raise MT5Wedged instead of starting a second call on the same
    non-threadsafe connection. `allow_wedged` skips the guard; only
    restart_terminal's reconnect uses it, after killing the terminal the
    stuck thread was talking to.
    """
    if not allow_wedged:
        _, oldest = _sdk_worker_state()
        if oldest is not None:
            raise MT5Wedged(f"{oldest.fn_name} stuck {oldest.age():.0f}s")

    box: list = [None]
    err_box: list = [None]
    done = threading.Event()
    worker_key = _add_sdk_worker(name)

    def _worker():
        try:
            box[0] = fn()
        except BaseException as e:
            err_box[0] = e
        finally:
            # Deregister before waking the caller, so a call that finishes
            # right at its timeout is not still counted when the next call
            # checks the guard.
            _remove_sdk_worker(worker_key)
            done.set()

    t = threading.Thread(target=_worker, daemon=True, name=f"mt5-sdk-{name}")
    t.start()
    if not done.wait(timeout=timeout):
        raise MT5Timeout(f"call timed out after {timeout}s")
    if err_box[0] is not None:
        raise err_box[0]
    return box[0]


def m(fn, *args, _timeout=MT5_CALL_TIMEOUT, _allow_wedged=False, **kwargs):
    """Call an mt5.* function with a hard timeout and per-call timing log.

    Caller must already hold the MT5 lock (via session() or @with_mt5()).
    A call that runs past its timeout raises MT5Timeout (the handler answers
    504) and leaves its thread behind. While that thread is alive, later calls
    raise MT5Wedged without starting a thread (the handler answers 503 with
    Retry-After) until it returns or restart_terminal kills the terminal.
    """
    name = getattr(fn, "__name__", "?")
    rid = _req_id()
    t0 = time.monotonic()
    outcome = "ok"
    try:
        return _run_with_timeout(
            lambda: fn(*args, **kwargs), timeout=_timeout, name=name, allow_wedged=_allow_wedged,
        )
    except MT5Wedged as e:
        outcome = f"WEDGED ({e})"
        raise
    except MT5Timeout:
        outcome = "TIMEOUT"
        raise
    finally:
        dur = (time.monotonic() - t0) * 1000
        if outcome == "ok":
            log.info("%s mt5.%s dur_ms=%.1f", rid, name, dur)
        else:
            count, oldest_age = sdk_worker_snapshot()
            log.error(
                "%s mode=%s path=%s mt5.%s %s dur_ms=%.1f workers_alive=%d oldest_age_s=%.0f",
                rid, MODE, _req_path(), name, outcome, dur, count, oldest_age or 0.0,
            )


@contextmanager
def session(bypass_queue_cap=False):
    """Acquire the MT5 lock for the duration of the block.

    Bumps queue depth on entry; fast-fails with QueueFull if too many
    requests are already piled up, and with MT5LockTimeout if the lock is
    not free within SESSION_ACQUIRE_TIMEOUT. Releases lock + decrements
    depth on exit, regardless of how the block exits.

    `bypass_queue_cap` skips the queue-depth check but still waits for the
    lock. The health monitor uses it so client backpressure cannot stop it
    from checking the terminal.
    """
    depth = _bump_depth()
    try:
        if depth > MAX_QUEUE_DEPTH and not bypass_queue_cap:
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
            raise MT5LockTimeout("could not acquire MT5 lock")
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
        except MT5Wedged as e:  # before MT5Timeout, which it subclasses
            resp = jsonify({"error": f"mt5 call is wedged: {e}"})
            resp.headers["Retry-After"] = str(MT5_WEDGE_RETRY_AFTER_SECONDS)
            return resp, 503
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


def init_mt5(login=None, password=None, server=None, allow_wedged=False):
    """Initialize the MT5 connection.

    Caller must hold the MT5 lock (via session() or be in a @with_mt5()
    handler). Returns True/False based on SDK result. Returns False on a
    plain timeout rather than raising, since callers (startup, monitor,
    ensure_initialized) all want to retry rather than surface to clients.
    MT5Wedged propagates so handlers answer 503 with Retry-After.

    `allow_wedged` skips the single-flight guard. restart_terminal sets it on
    its reconnect, because the stuck thread from before the kill may never
    return and would otherwise block every reconnect.
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
        result = m(mt5.initialize, _timeout=INIT_TIMEOUT, _allow_wedged=allow_wedged, **kwargs)
    except MT5Wedged:
        raise
    except MT5Timeout:
        log.error("MT5 initialization timed out.")
        return False
    if result:
        log.info("MT5 initialized successfully.")
    else:
        log.error("MT5 initialization failed.")
    return result


# Last successful terminal_info snapshot, cached lock-free for cosmetic
# readers (e.g. chartctl template build stamp). Never authoritative.
LAST_TERMINAL_INFO = None


def ensure_initialized():
    """Probe + reconnect helper. Caller must hold the MT5 lock.

    Always False on a mode: backtest terminal. Its SDK is never attached, so a
    probe would fall through to init_mt5(), which starts terminal64.exe and
    holds the tester's single-instance lock on the data dir; the next backtest
    there then exits with an empty report.

    MT5Wedged propagates instead of becoming False, so @with_mt5 answers 503
    with Retry-After rather than the callers' generic "not initialized" 503.
    """
    global LAST_TERMINAL_INFO
    if MODE == "backtest":
        log.warning("MT5 SDK request refused on a mode:backtest terminal reason=backtest_mode")
        return False
    try:
        info = m(mt5.terminal_info, _timeout=15)
    except MT5Wedged:
        raise
    except MT5Timeout:
        info = None
    if info is not None:
        LAST_TERMINAL_INFO = info
    if info is None:
        log.warning("Terminal not responding, attempting full init...")
        account = get_first_account()
        if account:
            return init_mt5(**account)
        return init_mt5()

    # Terminal is running — check if actually logged in
    try:
        acc = m(mt5.account_info, _timeout=15)
    except MT5Wedged:
        raise
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
    except MT5Wedged:
        raise
    except MT5Timeout:
        return False


def _kill_terminal():
    """Kill this terminal's terminal64.exe, found by its exact executable path.

    The old WMI filter matched ``*\\<broker>\\*`` when no account was
    configured, so a restart terminated every terminal of the broker, and its
    psutil fallback matched a substring of the directory, so ``...\\live``
    also hit ``...\\live2``. chartctl/terminal_pid.py compares whole paths."""
    from mt5api.chartctl import terminal_pid

    procs = terminal_pid.matching_processes()
    killed = [pid for pid, _started in procs if _kill_pid(pid)]
    if killed:
        log.info("Terminal process killed (pid %s).", ", ".join(str(p) for p in killed))
        time.sleep(2)
    return bool(killed)


def _kill_pid(pid):
    """Kill one process by its id. taskkill when psutil may not (a non-elevated
    API next to an elevated terminal)."""
    try:
        proc = psutil.Process(pid)
        proc.kill()
        proc.wait(timeout=10)
        return True
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
        log.warning("psutil could not kill terminal pid %d (%s), trying taskkill...", pid, exc)
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("taskkill of terminal pid %d failed (%s)", pid, exc)
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


# Set while restart_terminal runs. The terminal generation watcher
# (monitor.py) sees the relaunched process within seconds, but restart_terminal
# reconnects and re-applies the WebRequest list itself, so the watcher waits.
RESTART_IN_PROGRESS = threading.Event()


def restart_terminal():
    """Kill terminal, relaunch, wait for journal 'started for', reconnect."""
    RESTART_IN_PROGRESS.set()
    try:
        return _restart_terminal()
    finally:
        RESTART_IN_PROGRESS.clear()


def _restart_terminal():
    """Kill terminal, relaunch, wait for journal 'started for', reconnect.

    Caller must hold the MT5 lock — this blocks the lock for ~minutes
    while waiting for the terminal to come back up, which is intentional:
    handler requests during a restart get fast-503'd via the queue-depth
    backpressure rather than piling up against a dead terminal.
    """
    log.info("Restarting terminal...")
    if _sdk_worker_state()[1] is not None:
        # mt5.shutdown would be a second concurrent call into the stuck
        # connection. Killing the terminal may also release the stuck thread.
        log.warning("SDK call still stuck, skipping mt5.shutdown and killing the terminal.")
    else:
        try:
            m(mt5.shutdown, _timeout=15)
        except MT5Timeout:
            log.warning("mt5.shutdown timed out — proceeding to kill anyway.")

    killed = _kill_terminal()
    if not killed:
        log.warning("No terminal process found, launching fresh.")

    # Apply the WebRequest allowlist while the terminal is down. MT5 rewrites
    # common.ini on exit, so this must happen after the kill and before launch.
    # No-op unless this terminal has a desired allowlist set.
    try:
        from mt5api.chartctl import webrequest as _wr

        applied = _wr.apply_from_desired(TERMINAL_DIR)
        if applied is not None:
            log.info("Applied WebRequest allowlist (%d URL(s)) to common.ini", applied)
    except Exception:
        log.exception("Failed to apply WebRequest allowlist; continuing restart")

    # MT5 rewrites assistant.ini on exit too, so the same window applies.
    terminal_mcp.apply(DISABLE_TERMINAL_MCP, TERMINAL_DIR, "restart")

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
    # A thread stuck before the kill may never return, so the reconnect skips
    # the single-flight guard.
    account = get_first_account()
    if account:
        result = init_mt5(**account, allow_wedged=True)
    else:
        result = init_mt5(allow_wedged=True)

    if not result:
        log.error("Terminal restarted but reconnection failed.")
        return False

    # Threads stuck on the killed terminal no longer block anything. Forget
    # them, or every later call raises MT5Wedged and the monitor restarts the
    # terminal again on each cycle.
    abandoned = _forget_sdk_workers()
    if abandoned:
        log.warning(
            "Dropped %d SDK call(s) stuck on the killed terminal from the wedge guard.",
            abandoned,
        )
    log.info("Terminal restarted successfully.")
    _reapply_webrequest_after_restart()
    return True


def _reapply_webrequest_after_restart():
    """The terminal in the VM forgets its WebRequest allowlist on every
    restart, and the API's own start-up re-apply has long since run."""
    try:
        from mt5api.chartctl import webrequest_apply

        webrequest_apply.reapply_in_background(
            webrequest_apply.gui_settle_seconds(), "terminal restart"
        )
    except Exception:  # noqa: BLE001 - the restart itself succeeded; never fail it here
        log.exception("WebRequest re-apply after restart could not start")


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
