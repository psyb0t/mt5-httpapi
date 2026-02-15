import json
import os
import threading

import MetaTrader5 as mt5

from mt5api.config import (
    TERMINAL_PATH, ACCOUNT_FILE, TERMINAL_FILE, BROKER, ACCOUNT,
    load_terminal_config, save_terminal_config,
    ORDER_TYPE_MAP, FILLING_MAP, TIME_MAP,
)

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


def get_account(name):
    accounts = load_accounts()
    return accounts.get(name)


def get_first_account():
    accounts = load_accounts()
    if not accounts:
        return None
    # Use configured account if set
    if ACCOUNT and ACCOUNT in accounts:
        return accounts[ACCOUNT]
    return next(iter(accounts.values()))


def switch_account(broker, account_name):
    """Update terminal.json to point to a different broker/account."""
    config = load_terminal_config()
    config["broker"] = broker
    config["account"] = account_name
    save_terminal_config(config)


def _run_with_timeout(fn, timeout=INIT_TIMEOUT):
    """Run fn() in a thread with a timeout. Returns fn's result or None if timed out."""
    result = [None]

    def _worker():
        result[0] = fn()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
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
    return _run_with_timeout(lambda: mt5.initialize(**kwargs))


def ensure_initialized():
    info = _run_with_timeout(mt5.terminal_info, timeout=5)
    if info is not None:
        return True
    account = get_first_account()
    if account:
        return init_mt5(**account)
    return init_mt5()


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
