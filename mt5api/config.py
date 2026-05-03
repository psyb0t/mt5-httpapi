import argparse
import json
import os
import re

import MetaTrader5 as mt5

HOST = "0.0.0.0"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PACKAGE_DIR)
ACCOUNT_FILE = os.path.join(BASE_DIR, "config", "accounts.json")
TERMINAL_FILE = os.path.join(BASE_DIR, "config", "terminal.json")
BROKERS_DIR = os.path.join(BASE_DIR, "terminals")


def _parse_args():
    parser = argparse.ArgumentParser(description="MT5 HTTP API")
    parser.add_argument("--broker", default=None)
    parser.add_argument("--account", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument(
        "--utc-offset",
        default=None,
        dest="utc_offset",
        help="Broker's UTC offset as a duration string ('3h', '3h30m', "
             "'-2h', '0', '90m'). MT5 returns timestamps in broker wall-clock "
             "time disguised as unix UTC; this offset normalizes them to real "
             "UTC on the wire. Negative values are allowed for west-of-UTC "
             "brokers.",
    )
    args, _ = parser.parse_known_args()
    return args


_DURATION_RE = re.compile(
    r"^\s*(?P<sign>[+-])?\s*"
    r"(?:(?P<h>\d+(?:\.\d+)?)\s*h)?\s*"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*m)?\s*"
    r"(?:(?P<s>\d+(?:\.\d+)?)\s*s)?\s*$",
    re.IGNORECASE,
)


def parse_duration_to_seconds(value):
    """Parse '3h', '3h30m', '-2h', '90m', '0' into integer seconds.

    Bare numbers (e.g. '3' or '3.5' or 3) are interpreted as HOURS for
    convenience — most brokers run on whole-hour offsets.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 3600))
    s = str(value).strip()
    if not s:
        return 0
    # Bare number → hours.
    try:
        return int(round(float(s) * 3600))
    except ValueError:
        pass
    m = _DURATION_RE.match(s)
    if not m or not (m.group("h") or m.group("m") or m.group("s")):
        raise ValueError(
            f"Invalid duration: {value!r}. "
            "Use '3h', '3h30m', '-2h', '90m', or a bare number (hours)."
        )
    h = float(m.group("h") or 0)
    minutes = float(m.group("m") or 0)
    secs = float(m.group("s") or 0)
    total = h * 3600 + minutes * 60 + secs
    if m.group("sign") == "-":
        total = -total
    return int(round(total))


def load_terminal_config():
    if os.path.exists(TERMINAL_FILE):
        with open(TERMINAL_FILE) as f:
            return json.load(f)
    return {"broker": "default", "account": ""}


_args = _parse_args()
_terminal_config = load_terminal_config()

BROKER = _args.broker or _terminal_config.get("broker", "default")
ACCOUNT = _args.account or _terminal_config.get("account", "")
PORT = _args.port or 6542
API_TOKEN = _args.token or os.environ.get("API_TOKEN", "")
UTC_OFFSET_RAW = _args.utc_offset if _args.utc_offset is not None else os.environ.get("UTC_OFFSET", "")
UTC_OFFSET_SECONDS = parse_duration_to_seconds(UTC_OFFSET_RAW)
UTC_OFFSET_HOURS = UTC_OFFSET_SECONDS / 3600.0

# Resolve TERMINAL_PATH: account-specific copy first, then base install
_candidates = []
if ACCOUNT:
    _candidates.append(os.path.join(BROKERS_DIR, BROKER, ACCOUNT, "terminal64.exe"))
_candidates.append(os.path.join(BROKERS_DIR, BROKER, "base", "terminal64.exe"))

TERMINAL_PATH = _candidates[0]
for _c in _candidates:
    if os.path.exists(_c):
        TERMINAL_PATH = _c
        break

TERMINAL_DIR = os.path.dirname(TERMINAL_PATH)
INI_FILE = os.path.join(TERMINAL_DIR, "mt5start.ini")
IDENTITY = f"{BROKER}/{ACCOUNT}" if ACCOUNT else BROKER
LOG_DIR = os.path.join(BASE_DIR, "logs")
FULL_LOG = os.path.join(LOG_DIR, "full.log")

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}

ORDER_TYPE_MAP = {
    "BUY": mt5.ORDER_TYPE_BUY,
    "SELL": mt5.ORDER_TYPE_SELL,
    "BUY_LIMIT": mt5.ORDER_TYPE_BUY_LIMIT,
    "SELL_LIMIT": mt5.ORDER_TYPE_SELL_LIMIT,
    "BUY_STOP": mt5.ORDER_TYPE_BUY_STOP,
    "SELL_STOP": mt5.ORDER_TYPE_SELL_STOP,
    "BUY_STOP_LIMIT": mt5.ORDER_TYPE_BUY_STOP_LIMIT,
    "SELL_STOP_LIMIT": mt5.ORDER_TYPE_SELL_STOP_LIMIT,
}

FILLING_MAP = {
    "FOK": mt5.ORDER_FILLING_FOK,
    "IOC": mt5.ORDER_FILLING_IOC,
    "RETURN": mt5.ORDER_FILLING_RETURN,
}

TIME_MAP = {
    "GTC": mt5.ORDER_TIME_GTC,
    "DAY": mt5.ORDER_TIME_DAY,
    "SPECIFIED": mt5.ORDER_TIME_SPECIFIED,
    "SPECIFIED_DAY": mt5.ORDER_TIME_SPECIFIED_DAY,
}
