"""Market order lifecycle — place via POST /orders, verify position appears,
close via DELETE /positions/<ticket>, verify it's gone.

Every test order is tagged with MT5_TEST_MAGIC so the session cleanup fixture
can purge it even if a test crashes mid-flight.
"""
from __future__ import annotations

import requests

from tests.real.helpers import find_position_by_magic, wait_until

# MT5 retcode 10009 = TRADE_RETCODE_DONE
RETCODE_DONE = 10009


def test_buy_market_open_then_close(client, config, cleanup_after):
    body = {
        "symbol": config["symbol"],
        "type": "BUY",
        "volume": config["volume"],
        "magic": config["magic"],
        "comment": "mt5-httpapi-test-buy",
        "deviation": 20,
        "type_filling": "IOC",
    }
    result = client.post("/orders", json=body)
    assert isinstance(result, dict), f"unexpected response shape: {result}"
    assert result.get("retcode") == RETCODE_DONE, f"order_send retcode != DONE: {result}"
    assert result.get("deal") or result.get("order"), f"no deal/order ticket: {result}"

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None, "BUY position did not appear within timeout"
    assert pos["symbol"] == config["symbol"]
    assert pos["volume"] == config["volume"]
    assert pos["type"] == 0, f"expected BUY position (type=0), got {pos['type']}"
    assert pos["magic"] == config["magic"]

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE, f"close retcode != DONE: {close}"

    gone = wait_until(lambda: find_position_by_magic(client, config["magic"]) is None)
    assert gone, "position still present after close"
    assert cleanup_after is None or True  # fixture must be referenced


def test_sell_market_open_then_close(client, config, cleanup_after):
    body = {
        "symbol": config["symbol"],
        "type": "SELL",
        "volume": config["volume"],
        "magic": config["magic"],
        "comment": "mt5-httpapi-test-sell",
        "deviation": 20,
        "type_filling": "IOC",
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"SELL order_send failed: {result}"

    pos = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos is not None, "SELL position did not appear within timeout"
    assert pos["type"] == 1, f"expected SELL position (type=1), got {pos['type']}"

    close = client.delete(f"/positions/{pos['ticket']}")
    assert close.get("retcode") == RETCODE_DONE, f"close retcode != DONE: {close}"
    assert cleanup_after is None or True


def test_order_invalid_symbol_does_not_500(client, config):
    """Unknown symbol must NOT 500. Either rejected at validation (4xx) or
    routed to MT5 which surfaces an error retcode in body."""
    base = client.base_url.rstrip("/")
    resp = requests.post(
        f"{base}/orders",
        json={
            "symbol": "DEFINITELY_NOT_A_REAL_SYMBOL_XYZ",
            "type": "BUY",
            "volume": config["volume"],
            "magic": config["magic"],
        },
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=15,
    )
    assert resp.status_code != 500, f"server crashed: {resp.text[:300]}"
    if resp.status_code == 200:
        body = resp.json()
        assert body.get("retcode") != RETCODE_DONE, "DONE retcode for fake symbol — broker took it?"


def test_order_missing_required_field_returns_400(client, config):
    base = client.base_url.rstrip("/")
    resp = requests.post(
        f"{base}/orders",
        json={"symbol": config["symbol"], "type": "BUY"},  # no volume
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text[:200]}"


def test_position_persists_until_explicit_close(client, config, cleanup_after):
    """Sanity: an open position must NOT disappear on its own between two
    /positions calls. Catches a regression where /positions returns stale
    or unfiltered data."""
    body = {
        "symbol": config["symbol"],
        "type": "BUY",
        "volume": config["volume"],
        "magic": config["magic"],
        "type_filling": "IOC",
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE

    pos1 = wait_until(lambda: find_position_by_magic(client, config["magic"]))
    assert pos1 is not None
    pos2 = find_position_by_magic(client, config["magic"])
    assert pos2 is not None and pos2["ticket"] == pos1["ticket"]
    assert cleanup_after is None or True
