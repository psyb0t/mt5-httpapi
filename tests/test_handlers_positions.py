"""Contract tests for /positions — the routes that modify and close open trades.

Same harness as the orders suite: `m(...)` is swapped for a recorder,
`to_dict` stays real, and every failure path asserts `order_send` was never
reached. A handler that errors *after* sending has already traded.

"""

from collections import namedtuple

import MetaTrader5 as mt5
import pytest

from mt5api.handlers import positions as positions_handler

Position = namedtuple("Position", "ticket symbol volume type sl tp price_open")
OrderResult = namedtuple("OrderResult", "retcode deal order volume price comment")
Tick = namedtuple("Tick", "bid ask")

TICKET = 777
ASK = 1.3050
BID = 1.3040
DEFAULT_DEVIATION = 20


def _position(**overrides):
    base = {
        "ticket": TICKET,
        "symbol": "GBPUSD",
        "volume": 0.50,
        "type": mt5.ORDER_TYPE_BUY,
        "sl": 1.2900,
        "tp": 1.3200,
        "price_open": 1.3000,
    }
    base.update(overrides)
    return Position(**base)


def _result(retcode=None, comment="ok"):
    return OrderResult(
        retcode=mt5.TRADE_RETCODE_DONE if retcode is None else retcode,
        deal=9,
        order=TICKET,
        volume=0.50,
        price=BID,
        comment=comment,
    )


@pytest.fixture
def positions(patch_handler):
    return patch_handler(positions_handler)


def test_list_positions_renders_an_empty_array_when_the_sdk_returns_none(
    api_client, positions
):
    positions.set("positions_get", None)

    resp = api_client.get("/positions")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_positions_forwards_the_symbol_filter(api_client, positions):
    positions.set("positions_get", [_position()])

    resp = api_client.get("/positions?symbol=GBPUSD")

    assert resp.status_code == 200
    assert positions.calls_to("positions_get")[0]["kwargs"] == {"symbol": "GBPUSD"}


def test_get_position_returns_404_for_an_unknown_ticket(api_client, positions):
    positions.set("positions_get", [])

    resp = api_client.get(f"/positions/{TICKET}")

    assert resp.status_code == 404


def test_closing_a_buy_sells_at_the_bid(api_client, positions):
    """The side inversion is the whole job of this handler. Closing a BUY with
    another BUY doubles the exposure instead of flattening it, and it is not
    visible in the status code — only in what reached the SDK.
    """
    positions.set("positions_get", [_position(type=mt5.ORDER_TYPE_BUY)])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}")

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["type"] == mt5.ORDER_TYPE_SELL
    assert sent["price"] == BID
    assert sent["position"] == TICKET


def test_closing_a_sell_buys_at_the_ask(api_client, positions):
    positions.set("positions_get", [_position(type=mt5.ORDER_TYPE_SELL)])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}")

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["type"] == mt5.ORDER_TYPE_BUY
    assert sent["price"] == ASK


def test_closing_without_a_body_closes_the_full_volume(api_client, positions):
    positions.set("positions_get", [_position(volume=0.50)])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}")

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["volume"] == 0.50
    assert sent["deviation"] == DEFAULT_DEVIATION


def test_a_partial_close_sends_only_the_requested_volume(api_client, positions):
    """A partial close that silently sends the full volume flattens a position
    the caller meant to keep half of.
    """
    positions.set("positions_get", [_position(volume=0.50)])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}", json={"volume": 0.20})

    assert resp.status_code == 200
    assert positions.sent_order()["volume"] == 0.20


def test_close_passes_a_custom_deviation_as_an_int(api_client, positions):
    positions.set("positions_get", [_position()])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}", json={"deviation": 50})

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["deviation"] == 50
    assert isinstance(sent["deviation"], int)


def test_close_of_an_unknown_position_sends_nothing(api_client, positions):
    positions.set("positions_get", [])
    positions.set("order_send", _result())

    resp = api_client.delete(f"/positions/{TICKET}")

    assert resp.status_code == 404
    assert positions.calls_to("order_send") == []


def test_a_broker_rejection_on_close_surfaces_the_comment(api_client, positions):
    positions.set("positions_get", [_position()])
    positions.set("symbol_info_tick", Tick(bid=BID, ask=ASK))
    positions.set(
        "order_send", _result(retcode=mt5.TRADE_RETCODE_DONE + 1, comment="no liquidity")
    )

    resp = api_client.delete(f"/positions/{TICKET}")

    assert resp.status_code == 500
    assert "no liquidity" in resp.get_json()["error"]


def test_update_position_sends_an_sltp_action_with_both_levels(api_client, positions):
    positions.set("positions_get", [_position(sl=1.2900, tp=1.3200)])
    positions.set("order_send", _result())

    resp = api_client.put(f"/positions/{TICKET}", json={"sl": 1.2950, "tp": 1.3250})

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["action"] == mt5.TRADE_ACTION_SLTP
    assert sent["sl"] == 1.2950
    assert sent["tp"] == 1.3250


def test_updating_only_the_stop_keeps_the_existing_take_profit(api_client, positions):
    """Sending sl alone must not reset tp to zero — that would drop the exit
    the trade was opened with.
    """
    positions.set("positions_get", [_position(sl=1.2900, tp=1.3200)])
    positions.set("order_send", _result())

    resp = api_client.put(f"/positions/{TICKET}", json={"sl": 1.2950})

    assert resp.status_code == 200
    sent = positions.sent_order()
    assert sent["sl"] == 1.2950
    assert sent["tp"] == 1.3200


def test_update_without_a_body_is_a_400_and_sends_nothing(api_client, positions):
    positions.set("positions_get", [_position()])
    positions.set("order_send", _result())

    resp = api_client.put(f"/positions/{TICKET}", json={})

    assert resp.status_code == 400
    assert positions.calls_to("order_send") == []


def test_update_of_an_unknown_position_sends_nothing(api_client, positions):
    positions.set("positions_get", [])
    positions.set("order_send", _result())

    resp = api_client.put(f"/positions/{TICKET}", json={"sl": 1.2950})

    assert resp.status_code == 404
    assert positions.calls_to("order_send") == []


@pytest.mark.parametrize(
    "method,payload",
    [
        ("delete", None),
        ("put", {"sl": 1.2950}),
    ],
)
def test_uninitialised_mt5_never_trades(api_client, patch_handler, method, payload):
    recorder = patch_handler(positions_handler, initialized=False)
    recorder.set("positions_get", [_position()])
    recorder.set("order_send", _result())

    call = getattr(api_client, method)
    resp = call(f"/positions/{TICKET}", json=payload) if payload else call(
        f"/positions/{TICKET}"
    )

    assert resp.status_code == 503
    assert recorder.calls_to("order_send") == []
