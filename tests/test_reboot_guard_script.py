"""scripts/reboot_guard.py: when a scheduled reboot may go ahead, and what it
logs. The HTTP fetch, the sleep and the clock are injected, so a wait of hours
runs instantly."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reboot_guard.py"
TERMINALS = [
    {"broker": "b1", "account": "a1", "port": 7001},
    {"broker": "b2", "account": "a2", "instance": "i2", "port": 7002},
]
IDLE = {"busy": False, "reasons": []}


def _busy(*reasons):
    return {"busy": True, "reasons": list(reasons)}


@pytest.fixture
def guard(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("reboot_guard_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "FULL_LOG", str(tmp_path / "full.log"))
    log = module.log
    monkeypatch.setattr(module, "log", lambda message: log(message, str(tmp_path / "full.log")))
    module.test_log = tmp_path / "full.log"
    module.test_flag = tmp_path / "reboot.draining"
    return module


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


def _scripted(answers):
    """A fetch that answers per port from a list of rounds, last round repeating."""
    calls = {"round": 0, "seen": 0}

    def fetch(port, token):
        round_answers = answers[min(calls["round"], len(answers) - 1)]
        answer = round_answers.get(port, IDLE)
        calls["seen"] += 1
        if calls["seen"] % len(TERMINALS) == 0:
            calls["round"] += 1
        return answer

    return fetch


def _run(guard, answers, max_postpone=3600):
    clock = Clock()
    outcome = guard.wait_until_idle(
        TERMINALS, "tok", max_postpone,
        fetch=_scripted(answers), sleep=clock.sleep, clock=clock,
        drain_flag=str(guard.test_flag),
    )
    log = guard.test_log.read_text() if guard.test_log.exists() else ""
    return outcome, clock, log


def test_an_idle_vm_reboots_after_the_drain_check(guard):
    outcome, clock, log = _run(guard, [{}])

    assert outcome == "idle"
    assert clock.sleeps == [guard.DRAIN_SETTLE_SECONDS]
    assert guard.test_flag.exists()
    assert log == ""


def test_a_busy_api_postpones_the_reboot_and_logs_why(guard):
    answers = [
        {7002: _busy("backtest abc running")},
        {7002: _busy("backtest abc running")},
        {},
    ]

    outcome, clock, log = _run(guard, answers)

    assert outcome == "idle"
    assert clock.sleeps == [guard.POLL_SECONDS, guard.POLL_SECONDS, guard.DRAIN_SETTLE_SECONDS]
    assert "reboot postponed (reason=busy" in log
    assert "b2/a2/i2: backtest abc running" in log
    assert log.count("reboot postponed") == 1, "an unchanged wait is not logged every poll"


def test_a_long_unchanged_wait_is_logged_again_every_few_minutes(guard):
    """The same write, a little older on each check, is still the same wait."""
    rounds = int(guard.REPEAT_LOG_SECONDS / guard.POLL_SECONDS) + 2
    answers = [
        {7001: _busy(f"write_in_flight PUT /webrequest ({n * guard.POLL_SECONDS}.5s)")}
        for n in range(rounds)
    ] + [{}]

    _, _, log = _run(guard, answers)

    assert log.count("reboot postponed") == 2


def test_a_write_that_starts_during_the_drain_check_keeps_the_reboot_waiting(guard):
    answers = [{}, {7001: _busy("write_in_flight POST /orders (0.1s)")}, {}, {}]

    outcome, clock, log = _run(guard, answers)

    assert outcome == "idle"
    assert clock.sleeps == [
        guard.DRAIN_SETTLE_SECONDS, guard.POLL_SECONDS, guard.DRAIN_SETTLE_SECONDS,
    ]
    assert "b1/a1/default: write_in_flight POST /orders" in log


def test_the_drain_flag_comes_down_while_the_reboot_waits(guard):
    seen = []

    def fetch(port, token):
        seen.append(guard.test_flag.exists())
        return IDLE if len(seen) <= len(TERMINALS) else _busy("backtest x running")

    clock = Clock()
    outcome = guard.wait_until_idle(
        TERMINALS, "tok", 100, fetch=fetch, sleep=clock.sleep, clock=clock,
        drain_flag=str(guard.test_flag),
    )

    assert outcome == "max_postpone"
    assert not guard.test_flag.exists()


def test_a_job_that_never_ends_cannot_hold_the_reboot_forever(guard):
    outcome, clock, log = _run(guard, [{7001: _busy("backtest stuck running")}], max_postpone=600)

    assert outcome == "max_postpone"
    assert clock.now >= 600
    assert "reason=max_postpone" in log
    assert "b1/a1/default: backtest stuck running" in log


def test_an_api_that_does_not_answer_counts_as_idle(guard):
    reasons = guard.busy_reasons(TERMINALS, "tok", fetch=lambda port, token: None)

    assert reasons == []


def test_the_token_is_sent_to_every_api(guard):
    seen = []

    guard.busy_reasons(TERMINALS, "tok", fetch=lambda port, token: seen.append((port, token)))

    assert seen == [(7001, "tok"), (7002, "tok")]


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [(None, 360 * 60), (90, 90 * 60), ("45", 45 * 60), (0, 0), (-5, 0), ("soon", 360 * 60)],
)
def test_reboot_max_postpone_is_read_in_minutes(guard, raw, seconds):
    cfg = {} if raw is None else {"reboot_max_postpone": raw}

    assert guard.max_postpone_seconds(cfg) == seconds


@pytest.mark.parametrize(
    ("cfg", "seconds"),
    [
        ({"reboot": {"max_postpone": 90}}, 90 * 60),
        ({"reboot": {"max_postpone": 0}}, 0),
        # The block wins over the deprecated key, even without its own value.
        ({"reboot": {"max_postpone": 30}, "reboot_max_postpone": 90}, 30 * 60),
        ({"reboot": {}, "reboot_max_postpone": 90}, 360 * 60),
        ({"reboot": {"max_postpone": "soon"}}, 360 * 60),
    ],
)
def test_reboot_max_postpone_is_read_from_the_reboot_block(guard, cfg, seconds):
    assert guard.max_postpone_seconds(cfg) == seconds
