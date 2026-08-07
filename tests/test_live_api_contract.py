"""HTTP-contract coverage for the flows exercised only by tests/real/ (needs a
live MT5 terminal, so CI never runs them). Same route -> handler -> mt5client
-> SDK path as tests/test_handlers_*.py, driven through the same
`api_client` + `patch_handler` harness from conftest.py — just with the SDK
scripted to mirror each tests/real/ flow instead of a single canned call.

Every fixture below is an obviously-fake placeholder (symbol 'TESTUSD',
volume 0.01, ticket 12345, magic 424242) — never a real broker name or price.

Multi-step flows (place a limit order then cancel it, open a position then
close it) live in ONE test function each. `RecordedMT5` (conftest.py) has no
native side_effect-list support, so the sequencing is done by re-`set()`-ing
the canned return between HTTP calls — each `set()` only changes what the
NEXT call to that SDK function sees, which is equivalent to a side_effect
list for a scripted, strictly-ordered flow like these.

Flow-by-flow accounting (real-suite file -> flow -> this file):

tests/real/test_account.py
  test_get_account                        -> already covered by
    tests/test_handlers_readonly.py::test_account_returns_the_logged_in_account

tests/real/test_ping_terminal.py
  test_ping_returns_ok                    -> already covered by
    tests/test_handlers_readonly.py::test_ping_needs_no_terminal_at_all
  test_get_terminal_metadata              -> test_terminal_returns_the_broker_metadata
  test_last_error_endpoint                -> test_error_endpoint_returns_the_last_sdk_error

tests/real/test_symbols.py
  test_list_symbols                       -> test_list_symbols_returns_the_tradeable_names
  test_symbol_info                        -> test_get_symbol_returns_tradeable_info
  test_volume_min_meets_config            -> same flow as test_symbol_info (config-only
                                              sanity check on top of the same GET
                                              /symbols/<symbol> response, no separate route)
  test_current_tick                       -> test_get_tick_returns_the_live_price

tests/real/test_rates.py
  test_get_rates_count                    -> test_get_rates_returns_the_requested_bar_count
  test_get_rates_timeframes               -> test_get_rates_returns_the_requested_bar_count
                                              (parametrized over timeframe strings)
  test_get_ticks                          -> test_get_ticks_returns_recent_ticks
  test_rates_invalid_timeframe            -> already covered by
    tests/test_handlers_validation.py::test_validation_returns_400[...invalid timeframe]

tests/real/test_rates_ta.py
  test_rates_ta_empty_indicators_returns_400 -> test_rates_ta_rejects_a_missing_or_empty_indicators_spec
                                              (parametrized over an empty indicators object and a
                                              missing body)
  test_rates_ta_missing_body_returns_400  -> test_rates_ta_rejects_a_missing_or_empty_indicators_spec
                                              (same parametrized test, other case)
  test_rates_ta_rsi_ema_atr                -> test_rates_ta_returns_bars_and_the_ta_payload
  test_rates_ta_macd                       -> test_rates_ta_returns_bars_and_the_ta_payload
                                              (same test covers the single-indicator macd shape;
                                              the wickworks passthrough is indicator-agnostic)

tests/real/test_market_order.py
  test_buy_market_open_then_close         -> test_buy_market_open_then_close_lifecycle
  test_sell_market_open_then_close        -> test_sell_market_open_then_close_lifecycle
  test_order_invalid_symbol_does_not_500  -> already covered by
    tests/test_handlers_orders.py::test_an_unknown_symbol_is_a_404_and_sends_nothing
  test_order_missing_required_field_returns_400 -> already covered by
    tests/test_handlers_orders.py::test_bad_create_requests_never_reach_the_broker
  test_position_persists_until_explicit_close -> folded into
    test_buy_market_open_then_close_lifecycle (asserts two consecutive GET
    /positions calls agree before the close)

tests/real/test_limit_order.py
  test_buy_limit_place_modify_cancel      -> test_buy_limit_place_modify_cancel_lifecycle
  test_sell_limit_place_and_cancel        -> test_sell_limit_place_and_cancel_lifecycle
  test_buy_stop_place_and_cancel          -> test_stop_orders_place_and_cancel_send_the_right_type
  test_sell_stop_place_and_cancel         -> test_stop_orders_place_and_cancel_send_the_right_type
  test_get_order_by_ticket                -> test_get_order_by_ticket_returns_the_pending_order
  test_get_order_not_found                -> already covered by
    tests/test_handlers_orders.py::test_get_order_returns_404_for_an_unknown_ticket

tests/real/test_position_management.py
  test_modify_sl_tp_on_open_position       -> test_modify_sl_tp_on_open_position_lifecycle
  test_modify_sl_only                      -> already covered by
    tests/test_handlers_positions.py::test_updating_only_the_stop_keeps_the_existing_take_profit
  test_get_position_by_ticket              -> test_get_position_by_ticket_returns_the_position
  test_get_position_not_found              -> already covered by
    tests/test_handlers_positions.py::test_get_position_returns_404_for_an_unknown_ticket
  test_modify_position_not_found           -> already covered by
    tests/test_handlers_positions.py::test_update_of_an_unknown_position_sends_nothing

tests/real/test_history.py
  test_history_orders_30d                  -> test_history_orders_returns_populated_rows
  test_history_deals_30d                   -> test_history_deals_returns_populated_rows
  test_history_orders_missing_params_returns_400 -> already covered by
    tests/test_handlers_readonly.py::test_history_requires_both_range_bounds
  test_history_deals_garbage_params_returns_400  -> already covered by
    tests/test_handlers_readonly.py::test_history_rejects_a_non_numeric_range
  test_history_after_a_trade_contains_our_magic  -> test_history_after_a_trade_contains_our_magic
"""

from collections import namedtuple

import MetaTrader5 as mt5
import pytest

from mt5api.handlers import history as history_handler
from mt5api.handlers import orders as orders_handler
from mt5api.handlers import positions as positions_handler
from mt5api.handlers import symbols as symbols_handler
from mt5api.handlers import terminal as terminal_handler

SYMBOL = "TESTUSD"
VOLUME = 0.01
MAGIC = 424242
TICKET = 12345

ASK = 1.10050
BID = 1.10030

Symbol = namedtuple("Symbol", "name")
SymbolInfo = namedtuple("SymbolInfo", "name volume_min volume_step digits ask bid")
Tick = namedtuple("Tick", "time bid ask")
Order = namedtuple(
    "Order",
    "ticket symbol volume_current price_open sl tp type type_time time_expiration magic",
)
Position = namedtuple("Position", "ticket symbol volume type sl tp price_open magic")
OrderResult = namedtuple("OrderResult", "retcode deal order volume price comment")
HistoryOrder = namedtuple("HistoryOrder", "ticket symbol type time_setup")
HistoryDeal = namedtuple("HistoryDeal", "ticket symbol type time magic")
TerminalInfo = namedtuple("TerminalInfo", "name company connected")

RANGE_QUERY = "from=1700000000&to=1700086400"


def _order(**overrides):
    base = {
        "ticket": TICKET,
        "symbol": SYMBOL,
        "volume_current": VOLUME,
        "price_open": 0.5000,
        "sl": 0.0,
        "tp": 0.0,
        "type": mt5.ORDER_TYPE_BUY_LIMIT,
        "type_time": mt5.ORDER_TIME_GTC,
        "time_expiration": 0,
        "magic": MAGIC,
    }
    base.update(overrides)
    return Order(**base)


def _position(**overrides):
    base = {
        "ticket": TICKET,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY,
        "sl": 0.0,
        "tp": 0.0,
        "price_open": ASK,
        "magic": MAGIC,
    }
    base.update(overrides)
    return Position(**base)


def _result(retcode=None, comment="ok"):
    return OrderResult(
        retcode=mt5.TRADE_RETCODE_DONE if retcode is None else retcode,
        deal=1,
        order=TICKET,
        volume=VOLUME,
        price=ASK,
        comment=comment,
    )


def _fake_bars(count, start=1_700_000_000, step=3600):
    bars = []
    for i in range(count):
        t = start + i * step
        o = 1.1000 + i * 0.0001
        bars.append((t, o, o + 0.0005, o - 0.0005, o + 0.0002, 100 + i, 1, 1000 + i))
    return bars


def _fake_ticks(count, start=1_700_000_000):
    ticks = []
    for i in range(count):
        t = start + i
        bid = 1.1000 + i * 0.0001
        ticks.append((t, bid, bid + 0.0002, 0.0, 1, t * 1000, 0, 0.01))
    return ticks


# ── tests/real/test_ping_terminal.py ─────────────────────────────────────
# test_ping_returns_ok -> already covered by
#   tests/test_handlers_readonly.py::test_ping_needs_no_terminal_at_all


def test_terminal_returns_the_broker_metadata(api_client, patch_handler):
    recorder = patch_handler(terminal_handler)
    recorder.set(
        "terminal_info",
        TerminalInfo(name="TestTerminal", company="TestBroker Ltd", connected=True),
    )

    resp = api_client.get("/terminal")

    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("name", "company", "connected"):
        assert key in body, f"/terminal missing key: {key}"
    assert body["connected"] is True


def test_error_endpoint_returns_the_last_sdk_error(api_client, patch_handler):
    # terminal.last_error does not call ensure_initialized, so no extra setup
    # beyond patch_handler's default (initialized=True) is needed.
    recorder = patch_handler(terminal_handler)
    recorder.set("last_error", (1, "no error"))

    resp = api_client.get("/error")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["message"] == "no error"


# ── tests/real/test_symbols.py ───────────────────────────────────────────


def test_list_symbols_returns_the_tradeable_names(api_client, patch_handler):
    recorder = patch_handler(symbols_handler)
    recorder.set("symbols_get", [Symbol(name=SYMBOL), Symbol(name="OTHERUSD")])

    resp = api_client.get("/symbols")

    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)
    assert SYMBOL in body


def test_get_symbol_returns_tradeable_info(api_client, patch_handler):
    recorder = patch_handler(symbols_handler)
    recorder.set(
        "symbol_info",
        SymbolInfo(name=SYMBOL, volume_min=0.01, volume_step=0.01, digits=4, ask=ASK, bid=BID),
    )

    resp = api_client.get(f"/symbols/{SYMBOL}")

    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("name", "volume_min", "volume_step", "digits", "ask", "bid"):
        assert key in body, f"/symbols/<symbol> missing key: {key}"
    assert body["name"] == SYMBOL
    assert body["volume_min"] > 0
    assert body["volume_step"] > 0
    assert body["digits"] >= 0
    assert body["ask"] > 0 and body["bid"] > 0
    assert body["ask"] >= body["bid"]


def test_get_tick_returns_the_live_price(api_client, patch_handler):
    recorder = patch_handler(symbols_handler)
    recorder.set("symbol_info_tick", Tick(time=1_700_000_000, bid=BID, ask=ASK))

    resp = api_client.get(f"/symbols/{SYMBOL}/tick")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ask"] > 0
    assert body["bid"] > 0
    assert body["time"] > 0


# ── tests/real/test_rates.py ─────────────────────────────────────────────


@pytest.mark.parametrize("timeframe,count", [("H1", 100), ("M5", 10), ("D1", 10)])
def test_get_rates_returns_the_requested_bar_count(api_client, patch_handler, timeframe, count):
    recorder = patch_handler(symbols_handler)
    recorder.set("copy_rates_from_pos", _fake_bars(count))

    resp = api_client.get(f"/symbols/{SYMBOL}/rates?timeframe={timeframe}&count={count}")

    assert resp.status_code == 200
    bars = resp.get_json()
    assert isinstance(bars, list)
    assert len(bars) == count
    for bar in bars[:3]:
        for key in ("time", "open", "high", "low", "close", "tick_volume", "spread"):
            assert key in bar, f"bar missing {key}"
        assert bar["high"] >= bar["low"]


def test_get_ticks_returns_recent_ticks(api_client, patch_handler):
    recorder = patch_handler(symbols_handler)
    recorder.set("copy_ticks_from", _fake_ticks(50))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?count=50")

    assert resp.status_code == 200
    ticks = resp.get_json()
    assert isinstance(ticks, list) and len(ticks) > 0
    for t in ticks[:3]:
        assert "time" in t
        assert "bid" in t or "ask" in t


# ── tests/real/test_rates_ta.py ──────────────────────────────────────────


@pytest.mark.parametrize("body_json", [{"indicators": {}}, None])
def test_rates_ta_rejects_a_missing_or_empty_indicators_spec(api_client, patch_handler, body_json):
    recorder = patch_handler(symbols_handler)

    resp = api_client.post(
        f"/symbols/{SYMBOL}/rates/ta?timeframe=H1&count=50",
        json=body_json,
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    # Validation must short-circuit before touching the SDK at all.
    assert not [c for c in recorder.calls if c["fn"].startswith("copy_rates")]


def test_rates_ta_returns_bars_and_the_ta_payload(api_client, patch_handler, monkeypatch):
    recorder = patch_handler(symbols_handler)
    recorder.set("copy_rates_from_pos", _fake_bars(200))

    captured_payload = {}

    def _fake_call_wickworks(payload):
        captured_payload.update(payload)
        return {"rsi14": [50.0]}, 200, None

    monkeypatch.setattr(symbols_handler, "_call_wickworks", _fake_call_wickworks)

    resp = api_client.post(
        f"/symbols/{SYMBOL}/rates/ta?timeframe=H1&count=200",
        json={"indicators": {"rsi14": {"type": "rsi", "params": {"period": 14}}}},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symbol"] == SYMBOL
    assert body["timeframe"] == "H1"
    assert isinstance(body["bars"], list) and len(body["bars"]) > 0
    assert body["ta"] is not None
    assert "rsi14" in body["ta"]

    # The wickworks payload must use the camelCase bar shape from
    # _bars_to_wickworks — exactly the translation a live-terminal test
    # cannot inspect.
    first_bar = captured_payload["bars"][0]
    assert "tickVolume" in first_bar
    assert "realVolume" in first_bar
    assert "tick_volume" not in first_bar


# ── tests/real/test_market_order.py ──────────────────────────────────────


def test_buy_market_open_then_close_lifecycle(api_client, patch_handler):
    """POST /orders (market BUY) -> position appears in two consecutive
    GET /positions reads (test_position_persists_until_explicit_close) ->
    DELETE /positions/<ticket> flattens it.
    """
    recorder = patch_handler(orders_handler)
    patch_handler(positions_handler)

    recorder.set("symbol_info_tick", Tick(time=1_700_000_000, bid=BID, ask=ASK))
    recorder.set("order_send", _result())

    open_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "BUY",
            "volume": VOLUME,
            "magic": MAGIC,
            "comment": "test-buy",
            "deviation": 20,
            "type_filling": "IOC",
        },
    )
    assert open_resp.status_code == 201
    opened = open_resp.get_json()
    assert opened["retcode"] == mt5.TRADE_RETCODE_DONE
    assert opened.get("deal") or opened.get("order")

    sent_open = recorder.calls_to("order_send")[0]["kwargs"]
    assert sent_open["action"] == mt5.TRADE_ACTION_DEAL
    assert sent_open["type"] == mt5.ORDER_TYPE_BUY
    assert sent_open["price"] == ASK
    assert sent_open["symbol"] == SYMBOL
    assert sent_open["volume"] == VOLUME
    assert sent_open["magic"] == MAGIC

    recorder.set("positions_get", [_position(type=mt5.ORDER_TYPE_BUY)])

    first = api_client.get("/positions").get_json()
    second = api_client.get("/positions").get_json()
    assert first == second, "a stable position must read the same across two GETs"
    pos = first[0]
    assert pos["ticket"] == TICKET
    assert pos["symbol"] == SYMBOL
    assert pos["volume"] == VOLUME
    assert pos["type"] == mt5.ORDER_TYPE_BUY
    assert pos["magic"] == MAGIC

    recorder.set("order_send", _result())
    close_resp = api_client.delete(f"/positions/{TICKET}")
    assert close_resp.status_code == 200
    assert close_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_close = recorder.calls_to("order_send")[-1]["kwargs"]
    assert sent_close["action"] == mt5.TRADE_ACTION_DEAL
    assert sent_close["type"] == mt5.ORDER_TYPE_SELL
    assert sent_close["price"] == BID
    assert sent_close["position"] == TICKET

    recorder.set("positions_get", [])
    assert api_client.get("/positions").get_json() == []


def test_sell_market_open_then_close_lifecycle(api_client, patch_handler):
    recorder = patch_handler(orders_handler)
    patch_handler(positions_handler)

    recorder.set("symbol_info_tick", Tick(time=1_700_000_000, bid=BID, ask=ASK))
    recorder.set("order_send", _result())

    open_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "SELL",
            "volume": VOLUME,
            "magic": MAGIC,
            "comment": "test-sell",
            "deviation": 20,
            "type_filling": "IOC",
        },
    )
    assert open_resp.status_code == 201
    assert open_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_open = recorder.calls_to("order_send")[0]["kwargs"]
    assert sent_open["type"] == mt5.ORDER_TYPE_SELL
    assert sent_open["price"] == BID
    assert sent_open["volume"] == VOLUME

    recorder.set("positions_get", [_position(type=mt5.ORDER_TYPE_SELL)])
    pos = api_client.get("/positions").get_json()[0]
    assert pos["type"] == mt5.ORDER_TYPE_SELL

    recorder.set("order_send", _result())
    close_resp = api_client.delete(f"/positions/{TICKET}")
    assert close_resp.status_code == 200
    assert close_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_close = recorder.calls_to("order_send")[-1]["kwargs"]
    assert sent_close["type"] == mt5.ORDER_TYPE_BUY
    assert sent_close["price"] == ASK


# ── tests/real/test_limit_order.py ───────────────────────────────────────


def test_buy_limit_place_modify_cancel_lifecycle(api_client, patch_handler):
    recorder = patch_handler(orders_handler)
    limit_price = 0.5000
    new_price = 0.5050

    recorder.set("order_send", _result())
    place_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "BUY_LIMIT",
            "volume": VOLUME,
            "price": limit_price,
            "magic": MAGIC,
            "comment": "test-buy-limit",
        },
    )
    assert place_resp.status_code == 201
    assert place_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_place = recorder.calls_to("order_send")[0]["kwargs"]
    assert sent_place["action"] == mt5.TRADE_ACTION_PENDING
    assert sent_place["type"] == mt5.ORDER_TYPE_BUY_LIMIT
    assert sent_place["price"] == limit_price
    assert sent_place["volume"] == VOLUME

    recorder.set("orders_get", [_order(price_open=limit_price)])
    order = api_client.get("/orders").get_json()[0]
    assert order["symbol"] == SYMBOL
    assert order["price_open"] == limit_price

    recorder.set("order_send", _result())
    mod_resp = api_client.put(f"/orders/{TICKET}", json={"price": new_price})
    assert mod_resp.status_code == 200
    assert mod_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_mod = recorder.calls_to("order_send")[1]["kwargs"]
    assert sent_mod["action"] == mt5.TRADE_ACTION_MODIFY
    assert sent_mod["order"] == TICKET
    assert sent_mod["price"] == new_price

    recorder.set("orders_get", [_order(price_open=new_price)])
    updated = api_client.get("/orders").get_json()[0]
    assert updated["price_open"] == new_price

    recorder.set("order_send", _result())
    cancel_resp = api_client.delete(f"/orders/{TICKET}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_cancel = recorder.calls_to("order_send")[2]["kwargs"]
    assert sent_cancel["action"] == mt5.TRADE_ACTION_REMOVE
    assert sent_cancel["order"] == TICKET

    recorder.set("orders_get", [])
    assert api_client.get("/orders").get_json() == []


def test_sell_limit_place_and_cancel_lifecycle(api_client, patch_handler):
    recorder = patch_handler(orders_handler)
    limit_price = 2.0000

    recorder.set("order_send", _result())
    place_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "SELL_LIMIT",
            "volume": VOLUME,
            "price": limit_price,
            "magic": MAGIC,
        },
    )
    assert place_resp.status_code == 201
    assert place_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_place = recorder.calls_to("order_send")[0]["kwargs"]
    assert sent_place["action"] == mt5.TRADE_ACTION_PENDING
    assert sent_place["type"] == mt5.ORDER_TYPE_SELL_LIMIT
    assert sent_place["price"] == limit_price
    assert sent_place["volume"] == VOLUME

    recorder.set("orders_get", [_order(type=mt5.ORDER_TYPE_SELL_LIMIT, price_open=limit_price)])
    assert api_client.get("/orders").get_json()

    recorder.set("order_send", _result())
    cancel_resp = api_client.delete(f"/orders/{TICKET}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_cancel = recorder.calls_to("order_send")[1]["kwargs"]
    assert sent_cancel["action"] == mt5.TRADE_ACTION_REMOVE
    assert sent_cancel["order"] == TICKET

    recorder.set("orders_get", [])
    assert api_client.get("/orders").get_json() == []


@pytest.mark.parametrize(
    "type_str,mt5_type",
    [
        ("BUY_STOP", "ORDER_TYPE_BUY_STOP"),
        ("SELL_STOP", "ORDER_TYPE_SELL_STOP"),
    ],
)
def test_stop_orders_place_and_cancel_send_the_right_type(
    api_client, patch_handler, type_str, mt5_type
):
    recorder = patch_handler(orders_handler)
    stop_price = 2.0000
    expected_type = getattr(mt5, mt5_type)

    recorder.set("order_send", _result())
    place_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": type_str,
            "volume": VOLUME,
            "price": stop_price,
            "magic": MAGIC,
        },
    )
    assert place_resp.status_code == 201
    assert place_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_place = recorder.calls_to("order_send")[0]["kwargs"]
    assert sent_place["action"] == mt5.TRADE_ACTION_PENDING
    assert sent_place["type"] == expected_type
    assert sent_place["volume"] == VOLUME

    recorder.set("orders_get", [_order(type=expected_type, price_open=stop_price)])
    assert api_client.get("/orders").get_json()

    recorder.set("order_send", _result())
    cancel_resp = api_client.delete(f"/orders/{TICKET}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_cancel = recorder.calls_to("order_send")[1]["kwargs"]
    assert sent_cancel["action"] == mt5.TRADE_ACTION_REMOVE


def test_get_order_by_ticket_returns_the_pending_order(api_client, patch_handler):
    recorder = patch_handler(orders_handler)
    recorder.set("orders_get", [_order(price_open=0.5000)])

    resp = api_client.get(f"/orders/{TICKET}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ticket"] == TICKET
    assert body["symbol"] == SYMBOL
    assert body["magic"] == MAGIC
    assert body["price_open"] == 0.5000


# ── tests/real/test_position_management.py ───────────────────────────────


def test_modify_sl_tp_on_open_position_lifecycle(api_client, patch_handler):
    recorder = patch_handler(orders_handler)
    patch_handler(positions_handler)

    entry = ASK
    sl = round(entry * 0.70, 4)
    tp = round(entry * 1.30, 4)

    recorder.set("symbol_info_tick", Tick(time=1_700_000_000, bid=BID, ask=ASK))
    recorder.set("order_send", _result())
    open_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "BUY",
            "volume": VOLUME,
            "magic": MAGIC,
            "comment": "test-sltp",
        },
    )
    assert open_resp.status_code == 201
    assert open_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    recorder.set("positions_get", [_position(price_open=entry)])
    pos = api_client.get("/positions").get_json()[0]
    assert pos["price_open"] == entry

    recorder.set("order_send", _result())
    mod_resp = api_client.put(f"/positions/{TICKET}", json={"sl": sl, "tp": tp})
    assert mod_resp.status_code == 200
    assert mod_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    sent_mod = recorder.calls_to("order_send")[1]["kwargs"]
    assert sent_mod["action"] == mt5.TRADE_ACTION_SLTP
    assert sent_mod["position"] == TICKET
    assert sent_mod["sl"] == sl
    assert sent_mod["tp"] == tp

    recorder.set("positions_get", [_position(price_open=entry, sl=sl, tp=tp)])
    updated = api_client.get("/positions").get_json()[0]
    assert updated["sl"] == sl
    assert updated["tp"] == tp

    recorder.set("order_send", _result())
    close_resp = api_client.delete(f"/positions/{TICKET}")
    assert close_resp.status_code == 200
    assert close_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE


def test_get_position_by_ticket_returns_the_position(api_client, patch_handler):
    recorder = patch_handler(positions_handler)
    recorder.set("positions_get", [_position()])

    resp = api_client.get(f"/positions/{TICKET}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ticket"] == TICKET
    assert body["symbol"] == SYMBOL
    assert body["magic"] == MAGIC
    assert body["volume"] == VOLUME


# ── tests/real/test_history.py ───────────────────────────────────────────


def test_history_orders_returns_populated_rows(api_client, patch_handler):
    recorder = patch_handler(history_handler)
    recorder.set(
        "history_orders_get",
        [HistoryOrder(ticket=TICKET, symbol=SYMBOL, type=mt5.ORDER_TYPE_BUY, time_setup=1_700_000_000)],
    )

    resp = api_client.get(f"/history/orders?{RANGE_QUERY}")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    for o in data[:3]:
        for key in ("ticket", "symbol", "type", "time_setup"):
            assert key in o, f"history order missing {key}: {o}"


def test_history_deals_returns_populated_rows(api_client, patch_handler):
    recorder = patch_handler(history_handler)
    recorder.set(
        "history_deals_get",
        [HistoryDeal(
            ticket=TICKET, symbol=SYMBOL, type=mt5.ORDER_TYPE_BUY,
            time=1_700_000_000, magic=MAGIC,
        )],
    )

    resp = api_client.get(f"/history/deals?{RANGE_QUERY}")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    for d in data[:3]:
        for key in ("ticket", "symbol", "type", "time"):
            assert key in d, f"history deal missing {key}: {d}"


def test_history_after_a_trade_contains_our_magic(api_client, patch_handler):
    """Open + close a position, then verify the resulting deal shows up in
    /history/deals tagged with our magic number.
    """
    recorder = patch_handler(orders_handler)
    patch_handler(positions_handler)
    patch_handler(history_handler)

    recorder.set("symbol_info_tick", Tick(time=1_700_000_000, bid=BID, ask=ASK))
    recorder.set("order_send", _result())
    open_resp = api_client.post(
        "/orders",
        json={
            "symbol": SYMBOL,
            "type": "BUY",
            "volume": VOLUME,
            "magic": MAGIC,
            "comment": "test-history",
        },
    )
    assert open_resp.status_code == 201
    assert open_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    recorder.set("positions_get", [_position()])
    pos = api_client.get("/positions").get_json()[0]
    assert pos["ticket"] == TICKET

    recorder.set("order_send", _result())
    close_resp = api_client.delete(f"/positions/{TICKET}")
    assert close_resp.status_code == 200
    assert close_resp.get_json()["retcode"] == mt5.TRADE_RETCODE_DONE

    recorder.set(
        "history_deals_get",
        [HistoryDeal(
            ticket=TICKET, symbol=SYMBOL, type=mt5.ORDER_TYPE_BUY,
            time=1_700_000_000, magic=MAGIC,
        )],
    )
    resp = api_client.get(f"/history/deals?{RANGE_QUERY}")
    assert resp.status_code == 200
    deals = resp.get_json()
    matching = [d for d in deals if int(d.get("magic", 0)) == MAGIC]
    assert matching, f"no deals found tagged with magic {MAGIC}"
