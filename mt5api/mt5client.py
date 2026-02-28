import json
import os
import subprocess
import threading
import time
from datetime import date

import MetaTrader5 as mt5
import psutil
from mt5api.config import (
    ACCOUNT,
    ACCOUNT_FILE,
    BROKER,
    FILLING_MAP,
    INI_FILE,
    ORDER_TYPE_MAP,
    TERMINAL_DIR,
    TERMINAL_PATH,
    TIME_MAP,
)
from mt5api.logger import log

INIT_TIMEOUT = 60


def load_accounts():
    if not os.path.exists(ACCOUNT_FILE):
        return {}
    with open(ACCOUNT_FILE, "r") as f:
        data = json.load(f)
    # New format: {broker: {account_name: {login, password, server}}}
    if BROKER in data:
        return data[BROKER]
    # Support old flat format: {login, password, server}
    if "login" in data:
        return {"default": data}
    return data


def get_first_account():
    accounts = load_accounts()
    if not accounts:
        return None
    if ACCOUNT and ACCOUNT in accounts:
        return accounts[ACCOUNT]
    return next(iter(accounts.values()))


def _run_with_timeout(fn, timeout=INIT_TIMEOUT):
    """Run fn() in a thread with a timeout. Returns fn's result or None if timed out."""
    result = [None]

    def _worker():
        result[0] = fn()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        log.warning("MT5 call timed out after %ds", timeout)
        return None
    return result[0]


def init_mt5(login=None, password=None, server=None):
    kwargs = {"path": TERMINAL_PATH}
    if login:
        kwargs["login"] = int(login)
    if password:
        kwargs["password"] = password
    if server:
        kwargs["server"] = server
    log.info("Initializing MT5 (login=%s, server=%s)...", login, server)
    result = _run_with_timeout(lambda: mt5.initialize(**kwargs))
    if result:
        log.info("MT5 initialized successfully.")
    else:
        log.error("MT5 initialization failed.")
    return result


def ensure_initialized():
    info = _run_with_timeout(mt5.terminal_info, timeout=15)
    if info is None:
        log.warning("Terminal not responding, attempting full init...")
        account = get_first_account()
        if account:
            return init_mt5(**account)
        return init_mt5()

    # Terminal is running — check if actually logged in
    acc = _run_with_timeout(mt5.account_info, timeout=15)
    if acc is not None and acc.login != 0:
        return True

    # Not logged in — try mt5.login()
    log.warning("Not logged in, attempting login...")
    account = get_first_account()
    if not account:
        return True
    return _run_with_timeout(
        lambda: mt5.login(
            login=int(account["login"]),
            password=account["password"],
            server=account["server"],
        ),
        timeout=INIT_TIMEOUT,
    )


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
    """Kill terminal, relaunch, wait for journal 'started for', reconnect."""
    log.info("Restarting terminal...")
    mt5.shutdown()

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
    return named_tuple._asdict()


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
