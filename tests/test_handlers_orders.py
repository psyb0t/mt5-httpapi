"""Contract tests for /orders — the routes that place and cancel real trades.

Everything reaches the SDK through `m(...)`, which the `patch_handler` fixture
swaps for a recorder. `build_order_request` and `to_dict` stay REAL, so these
assert the dict that would actually reach the broker.

The load-bearing assertions are the ones about `order_send`: which side, which
price, and — on every failure path — that it was never called at all.

"""

from collections import namedtuple

import MetaTrader5 as mt5
import pytest

from mt5api.handlers import orders as orders_handler

Order = namedtuple(
    "Order",
    "ticket symbol volume_current price_open sl tp type type_time time_expiration",
)
OrderResult = namedtuple("OrderResult", "retcode deal order volume price comment")
Tick = namedtuple("Tick", "bid ask")

TICKET = 12345
ASK = 1.2345
BID = 1.2340


def _order(**overrides):
    base = {
        "ticket": TICKET,
        "symbol": "EURUSD",
        "volume_current": 0.10,
        "price_open": 1.2000,
        "sl": 1.1900,
        "tp": 1.2100,
        "type": mt5.ORDER_TYPE_BUY_LIMIT,
        "type_time": mt5.ORDER_TIME_GTC,
        "time_expiration": 0,
    }
    base.update(overrides)
    return Order(**base)


def _result(retcode=None, comment="ok"):
    return OrderResult(
        retcode=mt5.TRADE_RETCODE_DONE if retcode is None else retcode,
        deal=1,
        order=TICKET,
        volume=0.10,
        price=ASK,
        comment=comment,
    )


@pytest.fixture
def orders(patch_handler):
    return patch_handler(orders_handler)


def test_list_orders_renders_an_empty_array_when_the_sdk_returns_none(api_client, orders):
    orders.set("orders_get", None)

    resp = api_client.get("/orders")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_orders_forwards_the_symbol_filter(api_client, orders):
    orders.set("orders_get", [_order()])

    resp = api_client.get("/orders?symbol=EURUSD")

    assert resp.status_code == 200
    assert orders.calls_to("orders_get")[0]["kwargs"] == {"symbol": "EURUSD"}


def test_get_order_returns_404_for_an_unknown_ticket(api_client, orders):
    orders.set("orders_get", [])

    resp = api_client.get(f"/orders/{TICKET}")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_market_buy_is_sent_at_the_ask(api_client, orders):
    """Side/price pairing is the most expensive thing to get wrong here: a BUY
    filled at bid is an instant loss on every ticket.
    """
    orders.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json={"symbol": "EURUSD", "type": "BUY", "volume": 0.1})

    assert resp.status_code == 201
    assert orders.sent_order()["price"] == ASK


def test_market_sell_is_sent_at_the_bid(api_client, orders):
    orders.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json={"symbol": "EURUSD", "type": "SELL", "volume": 0.1})

    assert resp.status_code == 201
    assert orders.sent_order()["price"] == BID


def test_an_explicit_price_skips_the_tick_poll(api_client, orders):
    orders.set("order_send", _result())

    resp = api_client.post(
        "/orders",
        json={"symbol": "EURUSD", "type": "BUY", "volume": 0.1, "price": 1.5},
    )

    assert resp.status_code == 201
    assert orders.calls_to("symbol_info_tick") == []
    assert orders.sent_order()["price"] == 1.5


def test_a_rejected_order_returns_200_not_201(api_client, orders):
    """201 means created. A broker rejection is a real response about a trade
    that did not happen, so it must not read as one that did.
    """
    orders.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    orders.set("order_send", _result(retcode=mt5.TRADE_RETCODE_DONE + 1))

    resp = api_client.post("/orders", json={"symbol": "EURUSD", "type": "BUY", "volume": 0.1})

    assert resp.status_code == 200


def test_order_send_returning_none_is_a_500(api_client, orders):
    orders.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    orders.set("order_send", None)
    orders.set("last_error", (1, "boom"))

    resp = api_client.post("/orders", json={"symbol": "EURUSD", "type": "BUY", "volume": 0.1})

    assert resp.status_code == 500
    assert "error" in resp.get_json()


@pytest.mark.parametrize(
    "body,expected_status,desc",
    [
        ({}, 400, "empty body"),
        ({"type": "BUY", "volume": 0.1}, 400, "missing symbol"),
        ({"symbol": "EURUSD", "volume": 0.1}, 400, "missing type"),
        ({"symbol": "EURUSD", "type": "BUY"}, 400, "missing volume"),
        ({"symbol": "EURUSD", "type": "SIDEWAYS", "volume": 0.1}, 400, "unknown type"),
    ],
)
def test_bad_create_requests_never_reach_the_broker(
    api_client, orders, body, expected_status, desc
):
    orders.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json=body)

    assert resp.status_code == expected_status, desc
    assert orders.calls_to("order_send") == [], f"{desc}: order_send was called"


def test_an_unknown_symbol_is_a_404_and_sends_nothing(api_client, patch_handler):
    recorder = patch_handler(orders_handler, symbol_known=False)
    recorder.set("order_send", _result())

    resp = api_client.post("/orders", json={"symbol": "NOPE", "type": "BUY", "volume": 0.1})

    assert resp.status_code == 404
    assert recorder.calls_to("order_send") == []


def test_uninitialised_mt5_is_a_503_and_sends_nothing(api_client, patch_handler):
    recorder = patch_handler(orders_handler, initialized=False)
    recorder.set("order_send", _result())

    resp = api_client.post("/orders", json={"symbol": "EURUSD", "type": "BUY", "volume": 0.1})

    assert resp.status_code == 503
    assert recorder.calls_to("order_send") == []


def test_update_order_keeps_untouched_fields_from_the_live_order(api_client, orders):
    """A partial modify must not silently reset the fields it was not given —
    sending sl only should not wipe tp.
    """
    orders.set("orders_get", [_order(sl=1.1900, tp=1.2100)])
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"sl": 1.1950})

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["sl"] == 1.1950
    assert sent["tp"] == 1.2100
    assert sent["price"] == 1.2000
    assert sent["action"] == mt5.TRADE_ACTION_MODIFY


def test_update_order_surfaces_the_broker_comment_on_rejection(api_client, orders):
    orders.set("orders_get", [_order()])
    orders.set("order_send", _result(retcode=mt5.TRADE_RETCODE_DONE + 1, comment="market closed"))

    resp = api_client.put(f"/orders/{TICKET}", json={"sl": 1.1950})

    assert resp.status_code == 500
    assert "market closed" in resp.get_json()["error"]


def test_update_of_an_unknown_order_sends_nothing(api_client, orders):
    orders.set("orders_get", [])
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"sl": 1.1950})

    assert resp.status_code == 404
    assert orders.calls_to("order_send") == []


def test_cancel_order_removes_the_right_ticket(api_client, orders):
    orders.set("orders_get", [_order()])
    orders.set("order_send", _result())

    resp = api_client.delete(f"/orders/{TICKET}")

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["action"] == mt5.TRADE_ACTION_REMOVE
    assert sent["order"] == TICKET


def test_cancel_of_an_unknown_order_sends_nothing(api_client, orders):
    orders.set("orders_get", [])
    orders.set("order_send", _result())

    resp = api_client.delete(f"/orders/{TICKET}")

    assert resp.status_code == 404
    assert orders.calls_to("order_send") == []
