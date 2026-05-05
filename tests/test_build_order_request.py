"""Tests for build_order_request — translates HTTP JSON bodies into the
dict shape MT5's order_send expects. Pure logic, no broker calls.
"""

import pytest

import MetaTrader5 as mt5
from mt5api.mt5client import build_order_request


@pytest.mark.parametrize("body,expected_keys,desc", [
    # Minimal market buy — type_filling defaults to IOC when not provided
    ({"symbol": "EURUSD", "volume": 0.1, "type": "BUY"},
     {"symbol": "EURUSD", "volume": 0.1, "type": mt5.ORDER_TYPE_BUY,
      "type_filling": mt5.ORDER_FILLING_IOC},
     "minimal BUY defaults to IOC filling"),
    # Explicit FOK overrides default
    ({"symbol": "EURUSD", "volume": 0.1, "type": "SELL", "type_filling": "FOK"},
     {"symbol": "EURUSD", "volume": 0.1, "type": mt5.ORDER_TYPE_SELL,
      "type_filling": mt5.ORDER_FILLING_FOK},
     "explicit FOK overrides IOC default"),
    # Numeric coercion: strings with numeric values
    ({"volume": "0.5", "deviation": "20", "magic": "12345", "position": "999"},
     {"volume": 0.5, "deviation": 20, "magic": 12345, "position": 999,
      "type_filling": mt5.ORDER_FILLING_IOC},
     "numeric strings coerced"),
    # SL/TP/price floats
    ({"symbol": "EURUSD", "type": "BUY", "volume": 0.1,
      "price": 1.0950, "sl": 1.0850, "tp": 1.1050},
     {"symbol": "EURUSD", "type": mt5.ORDER_TYPE_BUY, "volume": 0.1,
      "price": 1.0950, "sl": 1.0850, "tp": 1.1050,
      "type_filling": mt5.ORDER_FILLING_IOC},
     "price/sl/tp passed through as floats"),
    # Pending order with type_time
    ({"symbol": "EURUSD", "type": "BUY_LIMIT", "volume": 0.1,
      "price": 1.08, "type_time": "GTC"},
     {"symbol": "EURUSD", "type": mt5.ORDER_TYPE_BUY_LIMIT, "volume": 0.1,
      "price": 1.08, "type_filling": mt5.ORDER_FILLING_IOC,
      "type_time": mt5.ORDER_TIME_GTC},
     "BUY_LIMIT with GTC time"),
    # Action mapping
    ({"action": "DEAL", "symbol": "EURUSD", "volume": 0.1, "type": "BUY"},
     {"action": mt5.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1,
      "type": mt5.ORDER_TYPE_BUY, "type_filling": mt5.ORDER_FILLING_IOC},
     "DEAL action mapped"),
    # Lowercase normalized
    ({"symbol": "EURUSD", "volume": 0.1, "type": "buy", "action": "deal"},
     {"action": mt5.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.1,
      "type": mt5.ORDER_TYPE_BUY, "type_filling": mt5.ORDER_FILLING_IOC},
     "lowercase type/action accepted"),
    # comment / position_by passthrough
    ({"symbol": "EURUSD", "volume": 0.1, "type": "BUY",
      "comment": "test trade", "position_by": "42"},
     {"symbol": "EURUSD", "volume": 0.1, "type": mt5.ORDER_TYPE_BUY,
      "comment": "test trade", "position_by": 42,
      "type_filling": mt5.ORDER_FILLING_IOC},
     "comment + position_by"),
])
def test_build_order_request_valid(body, expected_keys, desc):
    req, err = build_order_request(body)
    assert err is None
    assert req == expected_keys


@pytest.mark.parametrize("body,err_substr,desc", [
    ({"action": "BOGUS", "symbol": "EURUSD"},
     "Invalid action", "unknown action"),
    ({"symbol": "EURUSD", "type": "WIGGLE"},
     "Invalid type", "unknown order type"),
    ({"action": "deal", "type": "buy_limit_extra"},
     "Invalid type", "near-miss type spelling"),
])
def test_build_order_request_invalid(body, err_substr, desc):
    req, err = build_order_request(body)
    assert req is None
    assert err is not None
    assert err_substr in err


def test_unknown_filling_is_silently_dropped():
    """KNOWN QUIRK: an unknown type_filling string is currently neither
    rejected (no error) nor defaulted (no IOC fallback) — it just gets
    silently omitted from the request, letting MT5 choose. We assert the
    current behavior so a future fix that either errors or defaults to
    IOC will trip this test and force a deliberate choice.
    """
    req, err = build_order_request({
        "symbol": "EURUSD", "volume": 0.1, "type": "BUY",
        "type_filling": "BOGUS",
    })
    assert err is None
    assert req is not None
    assert "type_filling" not in req
