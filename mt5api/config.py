import json
import os

import MetaTrader5 as mt5

HOST = "0.0.0.0"
PORT = 6542

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(PACKAGE_DIR)
ACCOUNT_FILE = os.path.join(BASE_DIR, "account.json")
TERMINAL_FILE = os.path.join(BASE_DIR, "terminal.json")


def load_terminal_config():
    if os.path.exists(TERMINAL_FILE):
        with open(TERMINAL_FILE) as f:
            return json.load(f)
    return {"broker": "default", "account": ""}


def save_terminal_config(config):
    with open(TERMINAL_FILE, "w") as f:
        json.dump(config, f, indent=4)


_terminal_config = load_terminal_config()
BROKER = _terminal_config.get("broker", "default")
ACCOUNT = _terminal_config.get("account", "")

# terminal64.exe lives in <BASE_DIR>/<broker>/
TERMINAL_PATH = os.path.join(BASE_DIR, BROKER, "terminal64.exe")
# Backward compat: if broker subdir doesn't exist, check BASE_DIR directly
if not os.path.exists(TERMINAL_PATH):
    _legacy = os.path.join(BASE_DIR, "terminal64.exe")
    if os.path.exists(_legacy):
        TERMINAL_PATH = _legacy

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4, "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15, "M20": mt5.TIMEFRAME_M20, "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2, "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4, "H6": mt5.TIMEFRAME_H6, "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
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
