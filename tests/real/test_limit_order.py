"""Pending order lifecycle — place a BUY_LIMIT far below market so it can't
fill, verify it's in /orders, modify its price via PUT, then cancel via
DELETE. Same for SELL_LIMIT far above market.
"""
from __future__ import annotations

from tests.real.helpers import find_order_by_magic, wait_until

RETCODE_DONE = 10009


def _round_price(p: float, digits: int) -> float:
    return round(p, digits)


def test_buy_limit_place_modify_cancel(client, config, symbol_info, current_tick, cleanup_after):
    digits = symbol_info["digits"]
    bid = current_tick["bid"]
    limit_price = _round_price(bid * 0.50, digits)  # 50% below market

    body = {
        "symbol": config["symbol"],
        "type": "BUY_LIMIT",
        "volume": config["volume"],
        "price": limit_price,
        "magic": config["magic"],
        "comment": "test-buy-limit",
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"BUY_LIMIT placement failed: {result}"

    order = wait_until(lambda: find_order_by_magic(client, config["magic"]))
    assert order is not None, "BUY_LIMIT not visible in /orders"
    assert order["symbol"] == config["symbol"]
    assert abs(order["price_open"] - limit_price) < 10 ** (-digits) * 10

    new_price = _round_price(limit_price * 1.01, digits)
    mod = client.put(f"/orders/{order['ticket']}", json={"price": new_price})
    assert mod.get("retcode") == RETCODE_DONE, f"modify failed: {mod}"

    def _price_updated():
        o = find_order_by_magic(client, config["magic"])
        if not o:
            return None
        return abs(o["price_open"] - new_price) < 10 ** (-digits) * 10
    assert wait_until(_price_updated), "modify did not take effect"

    cancel = client.delete(f"/orders/{order['ticket']}")
    assert cancel.get("retcode") == RETCODE_DONE, f"cancel failed: {cancel}"

    gone = wait_until(lambda: find_order_by_magic(client, config["magic"]) is None)
    assert gone, "limit order still present after cancel"
    assert cleanup_after is None or True


def test_sell_limit_place_and_cancel(client, config, symbol_info, current_tick, cleanup_after):
    digits = symbol_info["digits"]
    ask = current_tick["ask"]
    limit_price = _round_price(ask * 1.50, digits)  # 50% above market

    body = {
        "symbol": config["symbol"],
        "type": "SELL_LIMIT",
        "volume": config["volume"],
        "price": limit_price,
        "magic": config["magic"],
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"SELL_LIMIT placement failed: {result}"

    order = wait_until(lambda: find_order_by_magic(client, config["magic"]))
    assert order is not None

    cancel = client.delete(f"/orders/{order['ticket']}")
    assert cancel.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_buy_stop_place_and_cancel(client, config, symbol_info, current_tick, cleanup_after):
    """BUY_STOP triggers when price RISES through level → must be ABOVE market."""
    digits = symbol_info["digits"]
    ask = current_tick["ask"]
    stop_price = _round_price(ask * 1.50, digits)

    body = {
        "symbol": config["symbol"],
        "type": "BUY_STOP",
        "volume": config["volume"],
        "price": stop_price,
        "magic": config["magic"],
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"BUY_STOP placement failed: {result}"

    order = wait_until(lambda: find_order_by_magic(client, config["magic"]))
    assert order is not None

    cancel = client.delete(f"/orders/{order['ticket']}")
    assert cancel.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_get_order_by_ticket(client, config, symbol_info, cleanup_after):
    """GET /orders/<ticket> returns the same pending order as the list view.

    No /tick pre-call — order endpoint auto-selects the symbol. Limit price
    is derived from session-scoped symbol_info (ask/bid baked in there)
    rather than a per-test current_tick fetch.
    """
    digits = symbol_info["digits"]
    bid = symbol_info["bid"]
    assert bid > 0, f"symbol_info has no bid: {symbol_info}"
    limit_price = _round_price(bid * 0.50, digits)

    body = {
        "symbol": config["symbol"],
        "type": "BUY_LIMIT",
        "volume": config["volume"],
        "price": limit_price,
        "magic": config["magic"],
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"BUY_LIMIT failed: {result}"

    order = wait_until(lambda: find_order_by_magic(client, config["magic"]))
    assert order is not None

    single = client.get(f"/orders/{order['ticket']}")
    assert isinstance(single, dict), f"unexpected response: {single}"
    assert single["ticket"] == order["ticket"]
    assert single["symbol"] == config["symbol"]
    assert single["magic"] == config["magic"]
    assert abs(single["price_open"] - limit_price) < 10 ** (-digits) * 10

    cancel = client.delete(f"/orders/{order['ticket']}")
    assert cancel.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True


def test_get_order_not_found(client, config):
    """GET /orders/<bogus_ticket> -> 404, not 500."""
    import requests
    base = client.base_url.rstrip("/")
    resp = requests.get(
        f"{base}/orders/999999999",
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=10,
    )
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text[:200]}"
    _ = config


def test_sell_stop_place_and_cancel(client, config, symbol_info, current_tick, cleanup_after):
    """SELL_STOP triggers when price FALLS through level → must be BELOW market."""
    digits = symbol_info["digits"]
    bid = current_tick["bid"]
    stop_price = _round_price(bid * 0.50, digits)

    body = {
        "symbol": config["symbol"],
        "type": "SELL_STOP",
        "volume": config["volume"],
        "price": stop_price,
        "magic": config["magic"],
    }
    result = client.post("/orders", json=body)
    assert result.get("retcode") == RETCODE_DONE, f"SELL_STOP placement failed: {result}"

    order = wait_until(lambda: find_order_by_magic(client, config["magic"]))
    assert order is not None

    cancel = client.delete(f"/orders/{order['ticket']}")
    assert cancel.get("retcode") == RETCODE_DONE
    assert cleanup_after is None or True
