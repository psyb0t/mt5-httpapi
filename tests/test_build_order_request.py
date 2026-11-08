"""Tests for build_order_request — translates HTTP JSON bodies into the
dict shape MT5's order_send expects. Pure logic, no broker calls.
"""

import pytest

import MetaTrader5 as mt5
import mt5api.mt5client as mc
from mt5api.mt5client import build_order_request

EXPIRY_UTC = 1_800_000_000
BROKER_OFFSET = 10_800  # a broker three hours ahead of real UTC


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


@pytest.mark.parametrize("order_type,mt5_type", [
    ("BUY_STOP_LIMIT", mt5.ORDER_TYPE_BUY_STOP_LIMIT),
    ("SELL_STOP_LIMIT", mt5.ORDER_TYPE_SELL_STOP_LIMIT),
])
@pytest.mark.parametrize("stoplimit,expected", [
    (1.29, 1.29),
    ("1.29", 1.29),
    (2, 2.0),
])
def test_stoplimit_reaches_the_request_for_the_stop_limit_types(
    order_type, mt5_type, stoplimit, expected
):
    req, err = build_order_request({
        "symbol": "EURUSD", "volume": 0.1, "type": order_type,
        "price": 1.30, "stoplimit": stoplimit,
    })
    assert err is None
    assert req["type"] == mt5_type
    assert req["stoplimit"] == expected
    assert isinstance(req["stoplimit"], float)


def test_stoplimit_is_optional_for_the_other_types():
    req, err = build_order_request({"type": "BUY_LIMIT", "price": 1.1})
    assert err is None
    assert "stoplimit" not in req


@pytest.mark.parametrize("order_type", ["BUY_STOP_LIMIT", "SELL_STOP_LIMIT"])
@pytest.mark.parametrize("extra", [{}, {"stoplimit": 0}, {"stoplimit": 0.0}])
def test_a_stop_limit_type_without_a_limit_price_is_refused(order_type, extra):
    req, err = build_order_request({"type": order_type, "price": 1.30, **extra})
    assert req is None
    assert "stoplimit" in err


@pytest.mark.parametrize("stoplimit", [
    "abc", "", None, True, False, [1.2], {"price": 1.2}, -0.5, float("nan"), float("inf"), "nan",
])
def test_a_stoplimit_that_is_not_a_price_is_refused(stoplimit):
    req, err = build_order_request({"type": "BUY_LIMIT", "stoplimit": stoplimit})
    assert req is None
    assert "stoplimit" in err


@pytest.mark.parametrize("type_time,mt5_type", [
    ("SPECIFIED", mt5.ORDER_TIME_SPECIFIED),
    ("SPECIFIED_DAY", mt5.ORDER_TIME_SPECIFIED_DAY),
    ("specified", mt5.ORDER_TIME_SPECIFIED),
])
def test_expiration_is_shifted_into_broker_time(monkeypatch, type_time, mt5_type):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", BROKER_OFFSET)
    req, err = build_order_request({"type_time": type_time, "expiration": EXPIRY_UTC})
    assert err is None
    assert req["type_time"] == mt5_type
    assert req["expiration"] == EXPIRY_UTC + BROKER_OFFSET


@pytest.mark.parametrize("offset", [0, -18_000])
def test_expiration_follows_the_configured_offset(monkeypatch, offset):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", offset)
    req, err = build_order_request({"type_time": "SPECIFIED", "expiration": EXPIRY_UTC})
    assert err is None
    assert req["expiration"] == EXPIRY_UTC + offset


@pytest.mark.parametrize("expiration", [EXPIRY_UTC, str(EXPIRY_UTC), float(EXPIRY_UTC)])
def test_expiration_accepts_whole_seconds_however_they_are_written(monkeypatch, expiration):
    monkeypatch.setattr(mc, "UTC_OFFSET_SECONDS", 0)
    req, err = build_order_request({"type_time": "SPECIFIED", "expiration": expiration})
    assert err is None
    assert req["expiration"] == EXPIRY_UTC
    assert isinstance(req["expiration"], int)


def test_an_expiration_with_no_type_time_selects_specified():
    req, err = build_order_request({"expiration": EXPIRY_UTC})
    assert err is None
    assert req["type_time"] == mt5.ORDER_TIME_SPECIFIED


def test_an_expiration_keeps_the_type_time_the_caller_named():
    req, err = build_order_request({"type_time": "SPECIFIED_DAY", "expiration": EXPIRY_UTC})
    assert err is None
    assert req["type_time"] == mt5.ORDER_TIME_SPECIFIED_DAY


def test_no_expiration_means_no_expiration_key():
    req, err = build_order_request({"type_time": "GTC"})
    assert err is None
    assert "expiration" not in req


@pytest.mark.parametrize("type_time", ["SPECIFIED", "SPECIFIED_DAY"])
def test_a_specified_type_without_an_expiration_is_refused(type_time):
    req, err = build_order_request({"type_time": type_time})
    assert req is None
    assert "expiration" in err


@pytest.mark.parametrize("expiration", [
    "soon", "", "1.5", None, True, False, 1.5, 0, -1, [EXPIRY_UTC], float("nan"), float("inf"),
])
def test_an_expiration_that_is_not_whole_positive_seconds_is_refused(expiration):
    req, err = build_order_request({"type_time": "SPECIFIED", "expiration": expiration})
    assert req is None
    assert "expiration" in err
