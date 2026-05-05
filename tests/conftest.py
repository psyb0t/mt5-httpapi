"""Test setup. The MT5 Python SDK is Windows-only and won't pip-install on
Linux, so we inject a stub `MetaTrader5` module before any mt5api import.
The stub only carries the constants the rest of the package references at
import time — actual SDK calls are mocked per-test where needed.

We also clear sys.argv so config.py's argparse doesn't choke on pytest's
own flags (it uses parse_known_args, but the program name still needs to
be sane).
"""

import sys
import types
from unittest.mock import MagicMock


def _install_mt5_stub():
    if "MetaTrader5" in sys.modules:
        return
    mt5 = types.ModuleType("MetaTrader5")
    # Constants referenced by mt5api.config TIMEFRAME_MAP / ORDER_TYPE_MAP /
    # FILLING_MAP / TIME_MAP. Concrete values are arbitrary — tests only
    # care about identity, not what the broker sees.
    for i, name in enumerate([
        "TIMEFRAME_M1", "TIMEFRAME_M2", "TIMEFRAME_M3", "TIMEFRAME_M4",
        "TIMEFRAME_M5", "TIMEFRAME_M6", "TIMEFRAME_M10", "TIMEFRAME_M12",
        "TIMEFRAME_M15", "TIMEFRAME_M20", "TIMEFRAME_M30",
        "TIMEFRAME_H1", "TIMEFRAME_H2", "TIMEFRAME_H3", "TIMEFRAME_H4",
        "TIMEFRAME_H6", "TIMEFRAME_H8", "TIMEFRAME_H12",
        "TIMEFRAME_D1", "TIMEFRAME_W1", "TIMEFRAME_MN1",
        "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
        "ORDER_TYPE_BUY_LIMIT", "ORDER_TYPE_SELL_LIMIT",
        "ORDER_TYPE_BUY_STOP", "ORDER_TYPE_SELL_STOP",
        "ORDER_TYPE_BUY_STOP_LIMIT", "ORDER_TYPE_SELL_STOP_LIMIT",
        "ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN",
        "ORDER_TIME_GTC", "ORDER_TIME_DAY",
        "ORDER_TIME_SPECIFIED", "ORDER_TIME_SPECIFIED_DAY",
        "COPY_TICKS_ALL", "COPY_TICKS_INFO", "COPY_TICKS_TRADE",
        "TRADE_ACTION_DEAL", "TRADE_ACTION_PENDING",
        "TRADE_ACTION_SLTP", "TRADE_ACTION_REMOVE", "TRADE_ACTION_MODIFY",
        "TRADE_ACTION_CLOSE_BY",
    ]):
        setattr(mt5, name, i)
    # Callable surface — every test that needs real behavior mocks per-call.
    for fn in (
        "initialize", "shutdown", "last_error", "terminal_info",
        "symbol_info", "symbol_info_tick", "symbol_select", "symbols_get",
        "copy_rates_from", "copy_rates_from_pos", "copy_rates_range",
        "copy_ticks_from", "copy_ticks_range",
        "account_info", "positions_get", "orders_get",
        "history_orders_get", "history_deals_get",
        "order_send", "order_check",
    ):
        setattr(mt5, fn, MagicMock())
    sys.modules["MetaTrader5"] = mt5


sys.argv = ["pytest"]
_install_mt5_stub()
