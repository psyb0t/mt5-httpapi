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

import mt5api.mt5client as mt5client
from mt5api.handlers import orders as orders_handler

Order = namedtuple(
    "Order",
    "ticket symbol volume_current price_open price_stoplimit sl tp type type_time "
    "time_expiration",
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
        "price_stoplimit": 0.0,
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


# --- client_order_id: a retry must never place a second order ----------------

Live = namedtuple("Live", "ticket comment")
CID = "agent-run-7"


@pytest.fixture(autouse=True)
def _no_remembered_client_orders(monkeypatch):
    monkeypatch.setattr(orders_handler, "_recent_client_orders", {})


def _buy(**extra):
    return {"symbol": "EURUSD", "type": "BUY", "volume": 0.1, "price": 1.5, **extra}


def test_a_new_client_order_id_is_sent_in_the_comment(api_client, orders):
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json=_buy(client_order_id=CID))

    assert resp.status_code == 201
    assert orders.sent_order()["comment"] == CID
    body = resp.get_json()
    assert body["duplicate"] is False
    assert body["client_order_id"] == CID


def test_a_retry_after_an_accepted_send_replays_it_without_sending(api_client, orders):
    orders.set("order_send", _result())
    first = api_client.post("/orders", json=_buy(client_order_id=CID)).get_json()

    resp = api_client.post("/orders", json=_buy(client_order_id=CID))

    assert resp.status_code == 200
    assert len(orders.calls_to("order_send")) == 1
    body = resp.get_json()
    assert body["duplicate"] is True
    assert body["source"] == "recent_send"
    assert body["order"] == first["order"]
    assert body["retcode"] == mt5.TRADE_RETCODE_DONE


@pytest.mark.parametrize(
    ("sdk_fn", "source"),
    [
        ("orders_get", "pending_order"),
        ("positions_get", "position"),
        ("history_orders_get", "history_order"),
    ],
)
def test_an_id_already_on_the_account_is_not_sent_again(api_client, orders, sdk_fn, source):
    """The retry may reach a restarted process that remembers nothing; the
    broker's own orders, positions and history still carry the id."""
    orders.set(sdk_fn, [Live(ticket=999, comment="other"), Live(ticket=TICKET, comment=CID)])

    resp = api_client.post("/orders", json=_buy(client_order_id=CID))

    assert resp.status_code == 200
    assert orders.calls_to("order_send") == []
    body = resp.get_json()
    assert body["duplicate"] is True
    assert body["source"] == source
    assert body["existing"]["ticket"] == TICKET


def test_a_rejected_send_is_not_remembered_so_a_retry_sends(api_client, orders):
    orders.set("order_send", _result(retcode=-1))
    api_client.post("/orders", json=_buy(client_order_id=CID))

    orders.set("order_send", _result())
    resp = api_client.post("/orders", json=_buy(client_order_id=CID))

    assert resp.status_code == 201
    assert len(orders.calls_to("order_send")) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"client_order_id": ""},
        {"client_order_id": "x" * 32},
        {"client_order_id": "has space"},
        {"client_order_id": 42},
        {"client_order_id": CID, "comment": "something else"},
    ],
)
def test_a_bad_client_order_id_is_refused_before_any_lookup(api_client, orders, extra):
    resp = api_client.post("/orders", json=_buy(**extra))

    assert resp.status_code == 400
    assert orders.calls == []


def test_a_matching_comment_is_accepted_with_the_id(api_client, orders):
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json=_buy(client_order_id=CID, comment=CID))

    assert resp.status_code == 201


def test_without_a_client_order_id_nothing_is_looked_up(api_client, orders):
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json=_buy())

    assert resp.status_code == 201
    assert [c["fn"] for c in orders.calls] == ["order_send"]
    assert "duplicate" not in resp.get_json()


# --- stoplimit and expiration ------------------------------------------------

EXPIRY_UTC = 1_800_000_000
BROKER_OFFSET = 10_800  # a broker three hours ahead of real UTC
TIME_TYPES = {
    "SPECIFIED": mt5.ORDER_TIME_SPECIFIED,
    "SPECIFIED_DAY": mt5.ORDER_TIME_SPECIFIED_DAY,
}


@pytest.fixture
def broker_three_hours_ahead(monkeypatch):
    monkeypatch.setattr(mt5client, "UTC_OFFSET_SECONDS", BROKER_OFFSET)


def _pending(**extra):
    return {"symbol": "EURUSD", "type": "BUY_LIMIT", "volume": 0.1, "price": 1.1, **extra}


@pytest.mark.parametrize(
    ("order_type", "mt5_type"),
    [
        ("BUY_STOP_LIMIT", mt5.ORDER_TYPE_BUY_STOP_LIMIT),
        ("SELL_STOP_LIMIT", mt5.ORDER_TYPE_SELL_STOP_LIMIT),
    ],
)
def test_a_stop_limit_order_sends_its_limit_price(api_client, orders, order_type, mt5_type):
    """price is the stop trigger, stoplimit the limit order it places. Dropping
    the second price sends an order the broker cannot place."""
    orders.set("order_send", _result())

    resp = api_client.post("/orders", json=_pending(type=order_type, price=1.30, stoplimit=1.29))

    assert resp.status_code == 201
    sent = orders.sent_order()
    assert sent["type"] == mt5_type
    assert sent["price"] == 1.30
    assert sent["stoplimit"] == 1.29


@pytest.mark.parametrize("type_time", ["SPECIFIED", "SPECIFIED_DAY"])
def test_an_expiring_order_sends_its_expiry_in_broker_time(
    api_client, orders, broker_three_hours_ahead, type_time
):
    orders.set("order_send", _result())

    resp = api_client.post(
        "/orders", json=_pending(type_time=type_time, expiration=EXPIRY_UTC)
    )

    assert resp.status_code == 201
    sent = orders.sent_order()
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET
    assert sent["type_time"] == TIME_TYPES[type_time]


def test_an_expiry_is_not_shifted_for_a_broker_on_utc(api_client, orders, monkeypatch):
    monkeypatch.setattr(mt5client, "UTC_OFFSET_SECONDS", 0)
    orders.set("order_send", _result())

    api_client.post("/orders", json=_pending(type_time="SPECIFIED", expiration=EXPIRY_UTC))

    assert orders.sent_order()["expiration"] == EXPIRY_UTC


def test_an_expiration_without_a_type_time_makes_the_order_expire(api_client, orders):
    """The broker ignores an expiry on a GTC order, so naming one has to pick
    the type that reads it."""
    orders.set("order_send", _result())

    api_client.post("/orders", json=_pending(expiration=EXPIRY_UTC))

    assert orders.sent_order()["type_time"] == mt5.ORDER_TIME_SPECIFIED


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"type": "BUY_STOP_LIMIT"}, "stoplimit"),
        ({"type": "SELL_STOP_LIMIT"}, "stoplimit"),
        ({"type": "BUY_STOP_LIMIT", "stoplimit": 0}, "stoplimit"),
        ({"stoplimit": "abc"}, "stoplimit"),
        ({"stoplimit": -1.0}, "stoplimit"),
        ({"stoplimit": True}, "stoplimit"),
        ({"stoplimit": None}, "stoplimit"),
        ({"stoplimit": [1.2]}, "stoplimit"),
        ({"type_time": "SPECIFIED"}, "expiration"),
        ({"type_time": "SPECIFIED_DAY"}, "expiration"),
        ({"expiration": "soon"}, "expiration"),
        ({"expiration": 1.5}, "expiration"),
        ({"expiration": True}, "expiration"),
        ({"expiration": 0}, "expiration"),
        ({"expiration": -5}, "expiration"),
        ({"expiration": None}, "expiration"),
        ({"expiration": [EXPIRY_UTC]}, "expiration"),
    ],
)
def test_a_bad_stoplimit_or_expiration_is_a_400_and_sends_nothing(
    api_client, orders, extra, field
):
    resp = api_client.post("/orders", json=_pending(**extra))

    assert resp.status_code == 400
    assert field in resp.get_json()["error"]
    assert orders.calls_to("order_send") == []


def test_update_order_keeps_the_current_stoplimit_and_expiry_when_not_given(api_client, orders):
    """The modify request carries every field, so sending sl alone must send
    the order's own stoplimit and expiry back and not zero them."""
    orders.set(
        "orders_get",
        [
            _order(
                type=mt5.ORDER_TYPE_BUY_STOP_LIMIT,
                price_stoplimit=1.1950,
                type_time=mt5.ORDER_TIME_SPECIFIED,
                time_expiration=EXPIRY_UTC + BROKER_OFFSET,
            )
        ],
    )
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"sl": 1.1850})

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["stoplimit"] == 1.1950
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET
    assert sent["type_time"] == mt5.ORDER_TIME_SPECIFIED


def test_update_order_sets_a_new_stoplimit(api_client, orders):
    orders.set(
        "orders_get",
        [_order(type=mt5.ORDER_TYPE_SELL_STOP_LIMIT, price_stoplimit=1.1950)],
    )
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"stoplimit": 1.1975})

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["stoplimit"] == 1.1975
    assert sent["price"] == 1.2000


def test_update_order_sets_a_new_expiry_in_broker_time(
    api_client, orders, broker_three_hours_ahead
):
    orders.set("orders_get", [_order()])
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"expiration": EXPIRY_UTC})

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET
    assert sent["type_time"] == mt5.ORDER_TIME_SPECIFIED


def test_update_order_keeps_specified_day_when_only_the_expiry_changes(api_client, orders):
    orders.set(
        "orders_get",
        [
            _order(
                type_time=mt5.ORDER_TIME_SPECIFIED_DAY,
                time_expiration=EXPIRY_UTC - 86_400,
            )
        ],
    )
    orders.set("order_send", _result())

    api_client.put(f"/orders/{TICKET}", json={"expiration": EXPIRY_UTC})

    assert orders.sent_order()["type_time"] == mt5.ORDER_TIME_SPECIFIED_DAY


def test_update_order_to_a_specified_type_needs_an_expiry(api_client, orders):
    orders.set("orders_get", [_order(time_expiration=0)])
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"type_time": "SPECIFIED"})

    assert resp.status_code == 400
    assert "expiration" in resp.get_json()["error"]
    assert orders.calls_to("order_send") == []


def test_update_order_to_a_specified_type_reuses_the_expiry_it_already_has(api_client, orders):
    orders.set("orders_get", [_order(time_expiration=EXPIRY_UTC + BROKER_OFFSET)])
    orders.set("order_send", _result())

    resp = api_client.put(f"/orders/{TICKET}", json={"type_time": "SPECIFIED"})

    assert resp.status_code == 200
    sent = orders.sent_order()
    assert sent["type_time"] == mt5.ORDER_TIME_SPECIFIED
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"stoplimit": "abc"}, "stoplimit"),
        ({"stoplimit": -1}, "stoplimit"),
        ({"stoplimit": True}, "stoplimit"),
        ({"stoplimit": None}, "stoplimit"),
        ({"expiration": "soon"}, "expiration"),
        ({"expiration": 1.5}, "expiration"),
        ({"expiration": 0}, "expiration"),
        ({"expiration": False}, "expiration"),
        ({"expiration": None}, "expiration"),
    ],
)
def test_update_order_refuses_a_bad_stoplimit_or_expiration_before_any_sdk_call(
    api_client, orders, body, field
):
    resp = api_client.put(f"/orders/{TICKET}", json=body)

    assert resp.status_code == 400
    assert field in resp.get_json()["error"]
    assert orders.calls == []
