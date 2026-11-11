"""The engine against a fake terminal: one order per leg with the source's
magic and a client_order_id, and every check that skips a signal on a target
skips it there only."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from signals.config import Limits
from signals.engine import Engine, broker_symbol
from signals.state import State
from signals.terminal import TerminalClient
from tests.signals_helpers import RULES_YAML, FakeTerminal, message, rules_from

TWO_TARGETS = RULES_YAML + """
  - source: testchan
    broker: otherbroker
    account: demo
    sizing: {mode: fixed, lots: 0.05}
"""


class FakeClock:
    """A clock that only moves when the engine sleeps, so retry tests run
    instantly and the signal ages exactly as far as the retries waited."""

    def __init__(self) -> None:
        self.time = datetime.now(timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.time

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.time += timedelta(seconds=seconds)


def _engine(tmp_path, rules=None, terminal=None, clock=None):
    rules = rules or rules_from(tmp_path)
    terminal = terminal or FakeTerminal()
    clock = clock or FakeClock()
    client = TerminalClient("http://nginx", "fake-token", transport=terminal.transport())
    state = State(str(tmp_path / "state.sqlite"))
    return Engine(rules, client, state, sleep=clock.sleep, now=clock.now), terminal, state


def _run(engine, msg):
    asyncio.run(engine.handle(msg))


def test_a_signal_becomes_one_order_per_leg(tmp_path):
    engine, terminal, _ = _engine(tmp_path)
    _run(engine, message())
    assert [(t, o["client_order_id"], o["volume"], o["tp"]) for t, o in terminal.orders] == [
        ("testbroker/demo/default", "testchan:101:1", 0.05, 101.0),
        ("testbroker/demo/default", "testchan:101:2", 0.03, 102.0),
        ("testbroker/demo/default", "testchan:101:3", 0.02, 103.0),
    ]
    first = terminal.orders[0][1]
    assert first["symbol"] == "TESTUSD"
    assert first["type"] == "BUY"
    assert first["sl"] == 99.0
    assert first["magic"] == 900001
    assert first["deviation"] == 20


def test_every_target_of_the_source_trades_with_its_own_sizing(tmp_path):
    engine, terminal, _ = _engine(tmp_path, rules_from(tmp_path, TWO_TARGETS))
    _run(engine, message())
    other = [o for t, o in terminal.orders if t == "otherbroker/demo/default"]
    # fixed 0.05, no tp_split: five steps of 0.01 over three equal legs, the
    # two spare steps to the nearer legs
    assert [o["volume"] for o in other] == [0.02, 0.02, 0.01]


def test_a_message_is_handled_once(tmp_path):
    engine, terminal, _ = _engine(tmp_path)
    _run(engine, message())
    _run(engine, message())
    assert len(terminal.orders) == 3


def test_the_same_message_arriving_twice_at_once_trades_once(tmp_path):
    engine, terminal, _ = _engine(tmp_path)

    async def both():
        await asyncio.gather(engine.handle(message()), engine.handle(message()))

    asyncio.run(both())
    assert len(terminal.orders) == 3


def test_a_message_that_is_not_a_signal_sends_nothing(tmp_path):
    engine, terminal, state = _engine(tmp_path)
    _run(engine, message("Weekly recap: great month everyone"))
    assert terminal.orders == []
    assert state.is_seen("testchan", "101")


def test_an_old_message_is_not_traded(tmp_path):
    engine, terminal, _ = _engine(tmp_path)
    old = replace(message(), time=datetime.now(timezone.utc) - timedelta(hours=1))
    _run(engine, old)
    assert terminal.orders == []


def test_dry_run_logs_the_orders_and_sends_none(tmp_path, caplog):
    rules = rules_from(tmp_path, "dry_run: true\n" + RULES_YAML)
    engine, terminal, _ = _engine(tmp_path, rules)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert terminal.orders == []
    assert [r.client_order_id for r in caplog.records if r.msg == "dry run, order not sent"] == [
        "testchan:101:1",
        "testchan:101:2",
        "testchan:101:3",
    ]


def _skip_reasons(caplog):
    return [r.reason for r in caplog.records if r.msg == "signal skipped"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("TESTUSD BUY 100\nSL 100.50\nTP 101", "price_past_sl"),
        ("TESTUSD BUY 99\nSL 98\nTP 100.05", "price_past_tp"),
        ("TESTUSD SELL 100\nSL 99.5\nTP 98", "price_past_sl"),
        ("TESTUSD BUY 100\nTP 101", "no_sl"),
    ],
)
def test_a_signal_the_price_has_passed_is_skipped(tmp_path, caplog, text, reason):
    engine, terminal, _ = _engine(tmp_path)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message(text))
    assert terminal.orders == []
    assert _skip_reasons(caplog) == [reason]


def test_entry_slippage_beyond_the_limit_skips(tmp_path, caplog):
    rules = rules_from(tmp_path)
    target = replace(rules.targets[0], max_entry_slippage_points=Decimal("5"))
    engine, terminal, _ = _engine(tmp_path, replace(rules, targets=(target,)))
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        # ask is 100.10: 10 points past an entry of 100.00, the limit is 5.
        _run(engine, message("TESTUSD BUY 100.00\nSL 99\nTP 101"))
        # 100.05-100.20 holds the ask: no slippage.
        _run(engine, message("TESTUSD BUY 100.05-100.20\nSL 99\nTP 101", message_id="102"))
    assert _skip_reasons(caplog) == ["entry_slippage"]
    assert [o["client_order_id"] for _, o in terminal.orders] == ["testchan:102:1"]


def test_an_unknown_symbol_is_skipped(tmp_path, caplog):
    engine, terminal, _ = _engine(tmp_path)
    terminal.symbols.clear()
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert _skip_reasons(caplog) == ["unknown_symbol"]


def _with_limits(tmp_path, **limits):
    rules = rules_from(tmp_path)
    target = replace(rules.targets[0], limits=Limits(**limits))
    return replace(rules, targets=(target,))


def _leg(comment, magic=900001, symbol="TESTUSD", ticket=1):
    return {"ticket": ticket, "symbol": symbol, "magic": magic, "comment": comment}


def test_the_legs_of_one_signal_count_as_one_open_signal(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.positions = [_leg("testchan:90:1", ticket=1), _leg("testchan:90:2", ticket=2)]
    terminal.pending = [_leg("testchan:90:3", ticket=3)]
    engine, _, _ = _engine(tmp_path, _with_limits(tmp_path, max_open_per_source_symbol=2), terminal)
    _run(engine, message())
    assert len(terminal.orders) == 3


@pytest.mark.parametrize(
    ("limits", "positions", "reason"),
    [
        ({"max_open_per_source_symbol": 1}, [_leg("testchan:90:1")], "max_open_per_source_symbol"),
        (
            {"max_open_per_source": 2},
            [_leg("testchan:90:1", symbol="OTHERUSD"), _leg("testchan:91:1", symbol="THIRDUSD")],
            "max_open_per_source",
        ),
        ({"max_open_per_symbol": 1}, [_leg("otherchan:80:1", magic=900002)], "max_open_per_symbol"),
        ({"max_open_per_symbol": 1, "count_manual": True}, [_leg("", magic=0)], "max_open_per_symbol"),
    ],
)
def test_a_full_limit_drops_the_new_signal(tmp_path, caplog, limits, positions, reason):
    terminal = FakeTerminal()
    terminal.positions = positions
    engine, _, _ = _engine(tmp_path, _with_limits(tmp_path, **limits), terminal)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert terminal.orders == []
    assert _skip_reasons(caplog) == [reason]


def test_manual_trades_do_not_count_unless_asked(tmp_path):
    terminal = FakeTerminal()
    terminal.positions = [_leg("", magic=0), _leg("my own trade", magic=123)]
    engine, _, _ = _engine(tmp_path, _with_limits(tmp_path, max_open_per_symbol=1), terminal)
    _run(engine, message())
    assert len(terminal.orders) == 3


def test_a_netting_account_gets_one_order(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.account["margin_mode"] = 0
    engine, _, _ = _engine(tmp_path, terminal=terminal)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert [(o["client_order_id"], o["volume"], o["tp"]) for _, o in terminal.orders] == [
        ("testchan:101:1", 0.1, 101.0)
    ]
    # The TPs that got no position are in the log, not silently gone.
    planned = [r for r in caplog.records if r.msg == "legs planned"]
    assert [(r.legs, r.tps_without_leg) for r in planned] == [(["0.10"], 2)]


def test_a_down_terminal_fails_only_its_own_target(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.unreachable.add("testbroker/demo/default")
    engine, _, state = _engine(tmp_path, rules_from(tmp_path, TWO_TARGETS), terminal)
    with caplog.at_level(logging.WARNING, logger="signals.engine"):
        _run(engine, message())
    assert {t for t, _ in terminal.orders} == {"otherbroker/demo/default"}
    assert any(r.msg == "terminal call failed, signal not traded" for r in caplog.records)
    assert state.is_seen("testchan", "101")


@pytest.mark.parametrize(
    ("failure", "error_name"),
    [(None, "TerminalUnreachable"), (503, "TerminalRejected")],
)
def test_a_failed_leg_does_not_stop_the_later_legs(tmp_path, caplog, failure, error_name):
    terminal = FakeTerminal()
    terminal.failing_orders["testchan:101:2"] = failure
    engine, _, state = _engine(tmp_path, terminal=terminal)
    with caplog.at_level(logging.WARNING, logger="signals.engine"):
        _run(engine, message())
    assert [o["client_order_id"] for _, o in terminal.orders] == [
        "testchan:101:1",
        "testchan:101:3",
    ]
    failed = [r for r in caplog.records if r.msg == "terminal call failed, leg not sent"]
    assert [(r.leg, r.client_order_id, r.reason, r.terminal) for r in failed] == [
        (2, "testchan:101:2", error_name, "testbroker/demo/default")
    ]
    assert state.is_seen("testchan", "101")


def test_a_signal_posted_during_a_reboot_trades_once_the_terminal_is_back(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.outage_requests = 3
    clock = FakeClock()
    engine, _, state = _engine(tmp_path, terminal=terminal, clock=clock)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert [o["client_order_id"] for _, o in terminal.orders] == [
        "testchan:101:1",
        "testchan:101:2",
        "testchan:101:3",
    ]
    assert clock.sleeps == [15.0, 15.0, 15.0]
    retries = [r for r in caplog.records if r.msg == "terminal down, retrying"]
    assert [r.reason for r in retries] == ["TerminalUnreachable"] * 3
    assert not any(r.msg == "terminal call failed, signal not traded" for r in caplog.records)
    assert state.is_seen("testchan", "101")


def test_a_leg_refused_before_a_reboot_is_sent_again(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.reboot_pending_orders["testchan:101:2"] = 2
    clock = FakeClock()
    engine, _, _ = _engine(tmp_path, terminal=terminal, clock=clock)
    with caplog.at_level(logging.INFO, logger="signals.engine"):
        _run(engine, message())
    assert [o["client_order_id"] for _, o in terminal.orders] == [
        "testchan:101:1",
        "testchan:101:2",
        "testchan:101:3",
    ]
    assert clock.sleeps == [15.0, 15.0]
    retries = [r for r in caplog.records if r.msg == "terminal down, retrying"]
    assert [(r.leg, r.reason) for r in retries] == [(2, "TerminalRejected")] * 2


def test_a_rejection_that_is_not_a_reboot_is_not_retried(tmp_path):
    terminal = FakeTerminal()
    terminal.failing_orders["testchan:101:2"] = 400
    clock = FakeClock()
    engine, _, _ = _engine(tmp_path, terminal=terminal, clock=clock)
    _run(engine, message())
    assert [o["client_order_id"] for _, o in terminal.orders] == [
        "testchan:101:1",
        "testchan:101:3",
    ]
    assert clock.sleeps == []


def test_retries_stop_before_the_signal_is_too_old_to_trade(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.unreachable.add("testbroker/demo/default")
    clock = FakeClock()
    engine, _, state = _engine(tmp_path, terminal=terminal, clock=clock)
    with caplog.at_level(logging.WARNING, logger="signals.engine"):
        _run(engine, message())
    assert terminal.orders == []
    # The default max_signal_age is five minutes: the last retry starts while
    # the signal is still young enough to trade when it finishes.
    assert sum(clock.sleeps) <= 300
    assert len(clock.sleeps) == 20
    assert any(r.msg == "terminal call failed, signal not traded" for r in caplog.records)
    assert state.is_seen("testchan", "101")


def test_a_source_with_a_longer_max_signal_age_trades_an_older_signal(tmp_path):
    # An H4 channel: a signal eight minutes old is still worth taking, while
    # the five minute default would drop it.
    text = RULES_YAML.replace(
        "    magic: 900001\n", "    magic: 900001\n    max_signal_age: 10m\n", 1
    )
    engine, terminal, _ = _engine(tmp_path, rules_from(tmp_path, text))
    eight_minutes_old = replace(message(), time=datetime.now(timezone.utc) - timedelta(minutes=8))
    _run(engine, eight_minutes_old)
    assert len(terminal.orders) == 3


def test_a_signal_older_than_its_source_max_signal_age_is_not_traded(tmp_path):
    text = RULES_YAML.replace(
        "    magic: 900001\n", "    magic: 900001\n    max_signal_age: 10m\n", 1
    )
    engine, terminal, _ = _engine(tmp_path, rules_from(tmp_path, text))
    _run(engine, replace(message(), time=datetime.now(timezone.utc) - timedelta(minutes=11)))
    assert terminal.orders == []


def test_submit_returns_while_a_down_terminal_is_still_being_retried(tmp_path):
    terminal = FakeTerminal()
    terminal.outage_requests = 1
    release = asyncio.Event()
    clock = FakeClock()

    async def held_sleep(seconds):
        await clock.sleep(seconds)
        await release.wait()

    rules = rules_from(tmp_path)
    client = TerminalClient("http://nginx", "fake-token", transport=terminal.transport())
    state = State(str(tmp_path / "state.sqlite"))
    engine = Engine(rules, client, state, sleep=held_sleep, now=clock.now)

    async def scenario():
        await engine.submit(message())
        for _ in range(20):
            await asyncio.sleep(0)
        # The source got control back while the retry is still waiting.
        assert clock.sleeps == [15.0]
        assert terminal.orders == []
        release.set()
        await engine.drain()

    asyncio.run(scenario())
    assert len(terminal.orders) == 3


def test_a_broker_refusal_is_logged(tmp_path, caplog):
    terminal = FakeTerminal()
    terminal.order_status = 200
    engine, _, _ = _engine(tmp_path, terminal=terminal)
    with caplog.at_level(logging.WARNING, logger="signals.engine"):
        _run(engine, message())
    refused = [r for r in caplog.records if r.msg == "broker refused the order"]
    assert [r.retcode for r in refused] == [10018, 10018, 10018]


def test_the_symbol_maps_apply_in_order(tmp_path):
    rules = rules_from(tmp_path)
    target = rules.targets[0]
    assert broker_symbol(rules, target, "TESTGOLD") == "TESTUSD"
    assert broker_symbol(rules, replace(target, symbol_suffix=".x"), "TESTGOLD") == "TESTUSD.x"
    assert broker_symbol(rules, replace(target, symbols={"TESTUSD": "TUSD"}), "TESTGOLD") == "TUSD"
