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
        # Every order-result branch compares against TRADE_RETCODE_DONE. Without
        # it here, reaching a success path raises AttributeError on the stub, so
        # no test could assert one.
        "TRADE_RETCODE_DONE",
    ]):
        setattr(mt5, name, i)
    # Callable surface — every test that needs real behavior mocks per-call.
    # Each mock carries its own __name__: a bare MagicMock has none, and both
    # mt5client's per-call timing log and the test recorder identify SDK calls
    # by `getattr(fn, "__name__", "?")` — without it everything is "?".
    for fn in (
        "initialize", "shutdown", "last_error", "terminal_info",
        "symbol_info", "symbol_info_tick", "symbol_select", "symbols_get",
        "copy_rates_from", "copy_rates_from_pos", "copy_rates_range",
        "copy_ticks_from", "copy_ticks_range",
        "account_info", "positions_get", "orders_get",
        "history_orders_get", "history_deals_get",
        "order_send", "order_check",
    ):
        mock = MagicMock()
        mock.__name__ = fn
        setattr(mt5, fn, mock)
    sys.modules["MetaTrader5"] = mt5


sys.argv = ["pytest"]
_install_mt5_stub()


# --- Handler-contract harness -------------------------------------------------
# Handlers reach the SDK exclusively through `m(fn, *args, **kwargs)`, imported
# into each handler module's namespace. Patching `m` per module gives one seam
# that both fakes the SDK and records every call, so a test can assert what
# actually reached `order_send` — the dict that reaches the broker.

import pytest  # noqa: E402  (must follow the stub install)


class RecordedMT5:
    """Stand-in for `m`. Serves canned returns by SDK function name and keeps
    every call for assertion. An `Exception` value is raised instead of returned.
    """

    def __init__(self):
        self.returns = {}
        self.calls = []

    def set(self, fn_name, value):
        self.returns[fn_name] = value
        return self

    def __call__(self, fn, *args, **kwargs):
        name = getattr(fn, "__name__", "?")
        self.calls.append({"fn": name, "args": args, "kwargs": kwargs})
        value = self.returns.get(name)
        if isinstance(value, Exception):
            raise value
        return value

    def calls_to(self, fn_name):
        return [c for c in self.calls if c["fn"] == fn_name]

    def sent_order(self):
        """The kwargs of the single `order_send` call. Fails loudly when the
        handler sent none or more than one — both are bugs worth catching.
        """
        sends = self.calls_to("order_send")
        assert len(sends) == 1, f"expected exactly 1 order_send, got {len(sends)}"
        return sends[0]["kwargs"]


@pytest.fixture
def mt5_calls():
    return RecordedMT5()


@pytest.fixture
def patch_handler(monkeypatch, mt5_calls):
    """Wire a handler module to the recorder. `initialized=False` exercises the
    503 path; `symbol_known=False` the 404 path.
    """

    def _patch(module, initialized=True, symbol_known=True):
        monkeypatch.setattr(module, "m", mt5_calls)
        monkeypatch.setattr(module, "ensure_initialized", lambda: initialized)
        if hasattr(module, "ensure_symbol"):
            monkeypatch.setattr(module, "ensure_symbol", lambda symbol: symbol_known)
        return mt5_calls

    return _patch


@pytest.fixture
def api_client():
    """The bare Flask test client.

    Named api_client, not client: two suites already define an enriched local
    `client` with extra per-module setup, and three fixtures sharing one name
    with different contracts is a reading trap.
    """
    from mt5api.server import app

    app.config["TESTING"] = True
    return app.test_client()
