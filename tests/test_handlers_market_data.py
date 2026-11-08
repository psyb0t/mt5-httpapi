"""Contract tests for the `count` handling on GET /symbols/<symbol>/ticks and
/rates: which ticks come back when there is no `from`, and that a `count` that
is not an integer is refused with a 400.

The SDK is scripted through the `patch_handler` recorder, so every assertion
is about what the handler asked the terminal for and what it returned.
"""

from datetime import datetime, timezone

import pytest

import mt5api.mt5client as mt5client
from mt5api.handlers import symbols as symbols_handler

SYMBOL = "TESTUSD"
NOW_UTC = 1_800_000_000
BROKER_OFFSET = 10_800  # a broker three hours ahead of real UTC


@pytest.fixture
def market(patch_handler, monkeypatch):
    monkeypatch.setattr(symbols_handler, "_now_utc_seconds", lambda: NOW_UTC)
    return patch_handler(symbols_handler)


def _broker_clock(utc_seconds, offset):
    return datetime.fromtimestamp(utc_seconds + offset, tz=timezone.utc)


def _ticks(count, end_utc, offset=0):
    """`count` SDK ticks one second apart, oldest first, the last one at
    `end_utc`. Times are broker time, like the terminal returns them."""
    ticks = []
    for i in range(count):
        utc = end_utc - (count - 1 - i)
        bid = 1.1000 + i * 0.0001
        ticks.append(
            (utc + offset, bid, bid + 0.0002, 0.0, 1, (utc + offset) * 1000, 6, 0.01)
        )
    return ticks


def _bars(count):
    return [
        (NOW_UTC - 3600 * (count - i), 1.1, 1.2, 1.0, 1.15, 100, 1, 1000) for i in range(count)
    ]


@pytest.mark.parametrize("count", ["3", "-3"])
@pytest.mark.parametrize("offset", [0, BROKER_OFFSET])
def test_ticks_without_from_are_the_latest_ones_up_to_now(
    api_client, market, monkeypatch, count, offset
):
    """copy_ticks_from reads forward from its start date, so asking it for
    |count| ticks from a fixed early date returned the OLDEST ticks the
    terminal holds."""
    monkeypatch.setattr(mt5client, "UTC_OFFSET_SECONDS", offset)
    market.set("copy_ticks_range", _ticks(10, NOW_UTC, offset))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?count={count}")

    assert resp.status_code == 200
    assert [t["time"] for t in resp.get_json()] == [NOW_UTC - 2, NOW_UTC - 1, NOW_UTC]
    assert market.calls_to("copy_ticks_from") == []
    (call,) = market.calls_to("copy_ticks_range")
    assert call["args"][2] == _broker_clock(NOW_UTC, offset)


def test_ticks_without_from_or_count_are_the_latest_hundred(api_client, market):
    market.set("copy_ticks_range", _ticks(150, NOW_UTC))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks")

    assert resp.status_code == 200
    times = [t["time"] for t in resp.get_json()]
    assert times == list(range(NOW_UTC - 99, NOW_UTC + 1))


def test_ticks_without_from_come_back_oldest_first_like_every_other_mode(api_client, market):
    market.set("copy_ticks_range", _ticks(10, NOW_UTC))

    times = [t["time"] for t in api_client.get(f"/symbols/{SYMBOL}/ticks?count=5").get_json()]

    assert times == sorted(times)


def test_ticks_without_from_widen_the_window_until_it_holds_enough(api_client, market):
    market.set("copy_ticks_range", _ticks(2, NOW_UTC))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?count=5")

    assert [t["time"] for t in resp.get_json()] == [NOW_UTC - 1, NOW_UTC]
    calls = market.calls_to("copy_ticks_range")
    assert len(calls) == symbols_handler.TICKS_BACKWARD_MAX_ATTEMPTS
    windows = [(c["args"][2] - c["args"][1]).total_seconds() for c in calls]
    first = symbols_handler.TICKS_BACKWARD_FLOOR_WINDOW_SEC
    assert windows == [first * 2**i for i in range(len(calls))]


def test_ticks_without_from_stop_widening_once_the_window_holds_enough(api_client, market):
    market.set("copy_ticks_range", _ticks(10, NOW_UTC))

    api_client.get(f"/symbols/{SYMBOL}/ticks?count=5")

    assert len(market.calls_to("copy_ticks_range")) == 1


def test_ticks_without_from_answer_empty_when_the_sdk_has_none(api_client, market):
    market.set("copy_ticks_range", None)

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?count=5")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_a_positive_count_with_from_still_reads_forward_from_the_anchor(api_client, market):
    market.set("copy_ticks_from", _ticks(4, 1_700_000_003))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?from=1700000000&count=4")

    assert resp.status_code == 200
    (call,) = market.calls_to("copy_ticks_from")
    assert call["args"][1] == _broker_clock(1_700_000_000, 0)
    assert call["args"][2] == 4
    assert market.calls_to("copy_ticks_range") == []


def test_a_negative_count_with_from_still_ends_at_the_anchor(api_client, market):
    market.set("copy_ticks_range", _ticks(10, 1_700_000_000))

    resp = api_client.get(f"/symbols/{SYMBOL}/ticks?from=1700000000&count=-3")

    assert [t["time"] for t in resp.get_json()] == [1_699_999_998, 1_699_999_999, 1_700_000_000]
    (call,) = market.calls_to("copy_ticks_range")
    assert call["args"][2] == _broker_clock(1_700_000_000, 0)


@pytest.mark.parametrize("route", ["rates", "ticks"])
@pytest.mark.parametrize("count", ["abc", "1.5", "1e3", "0x10", "ten", "--5", "5 5", "NaN"])
@pytest.mark.parametrize("anchor", [{}, {"from": "1700000000"}])
def test_a_count_that_is_not_an_integer_is_a_400_not_a_500(
    api_client, market, route, count, anchor
):
    resp = api_client.get(
        f"/symbols/{SYMBOL}/{route}", query_string={"count": count, **anchor}
    )

    assert resp.status_code == 400
    assert "count" in resp.get_json()["error"]
    assert [c for c in market.calls if c["fn"].startswith("copy_")] == []


def test_rates_ta_with_a_count_that_is_not_an_integer_is_a_400(api_client, market):
    resp = api_client.post(
        f"/symbols/{SYMBOL}/rates/ta?timeframe=H1&count=1.5",
        json={"indicators": {"rsi": True}},
    )

    assert resp.status_code == 400
    assert "count" in resp.get_json()["error"]
    assert [c for c in market.calls if c["fn"].startswith("copy_")] == []


@pytest.mark.parametrize("count", ["7", "-7", " 7 ", "+7"])
def test_integer_counts_in_any_spelling_still_work(api_client, market, count):
    market.set("copy_rates_from_pos", _bars(7))

    resp = api_client.get(f"/symbols/{SYMBOL}/rates", query_string={"count": count})

    assert resp.status_code == 200
    assert len(resp.get_json()) == 7
    assert market.calls_to("copy_rates_from_pos")[0]["args"][3] == 7


def test_an_empty_count_means_the_default_not_an_error(api_client, market):
    market.set("copy_rates_from_pos", _bars(100))

    resp = api_client.get(f"/symbols/{SYMBOL}/rates?count=")

    assert resp.status_code == 200
    assert market.calls_to("copy_rates_from_pos")[0]["args"][3] == 100


def test_a_backward_tick_read_reaches_across_a_weekend_close():
    """With 6 attempts the widest window was 32 minutes, so asking for the
    latest ticks on a Sunday came back empty; it must reach past a Friday
    close, more than 48 hours back."""
    widest = symbols_handler.TICKS_BACKWARD_FLOOR_WINDOW_SEC * 2 ** (
        symbols_handler.TICKS_BACKWARD_MAX_ATTEMPTS - 1
    )
    assert widest > 48 * 3600
