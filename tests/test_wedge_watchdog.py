"""Tests for mt5api.wedge_watchdog — see docs/spec/mt5-httpapi-sdk-call-thread-leak.md.

_watchdog_loop's exit_fn / run_lock_held / sleep_fn are all injected, so these
tests call it directly (not through the real daemon thread) with a fake clock
driven by sleep_fn, rather than sleeping in real time.
"""
from unittest.mock import MagicMock

import pytest

import mt5api.wedge_watchdog as ww


class FakeClock:
    """A controllable stand-in for time.monotonic(), advanced by sleep_fn."""

    def __init__(self):
        self.t = 0.0

    def advance(self, seconds):
        self.t += seconds

    def __call__(self):
        return self.t


class _StopLoop(Exception):
    """Raised by a bounded sleep_fn to break out of _watchdog_loop's `while
    True` once a test has seen enough iterations, for scenarios where the
    loop would otherwise never call exit_fn (and so never return on its own).
    """


def _bounded_sleep(clock, max_ticks):
    calls = {"n": 0}

    def _sleep(seconds):
        calls["n"] += 1
        clock.advance(seconds)
        if calls["n"] > max_ticks:
            raise _StopLoop()

    return _sleep


def test_watchdog_exits_once_the_oldest_worker_passes_the_threshold_and_not_before(monkeypatch):
    monkeypatch.setattr(ww, "MODE", "live")
    monkeypatch.setattr(ww, "MT5_WEDGE_EXIT_SECONDS", 100)
    statuses = iter([("mt5.initialize", 50.0), ("mt5.initialize", 150.0)])
    monkeypatch.setattr(ww, "sdk_wedge_status", lambda: next(statuses))
    exit_mock = MagicMock()

    ww._watchdog_loop(exit_mock, run_lock_held=lambda: False, sleep_fn=lambda s: None)

    exit_mock.assert_called_once_with(ww.WEDGE_EXIT_CODE)


def test_watchdog_does_nothing_while_no_worker_is_wedged(monkeypatch):
    monkeypatch.setattr(ww, "MT5_WEDGE_EXIT_SECONDS", 100)
    monkeypatch.setattr(ww, "sdk_wedge_status", lambda: None)
    exit_mock = MagicMock()
    clock = FakeClock()
    monkeypatch.setattr(ww.time, "monotonic", clock)

    with pytest.raises(_StopLoop):
        ww._watchdog_loop(exit_mock, run_lock_held=lambda: False, sleep_fn=_bounded_sleep(clock, 3))

    exit_mock.assert_not_called()


def test_watchdog_runs_in_backtest_mode_and_defers_exit_while_run_lock_is_held(monkeypatch):
    monkeypatch.setattr(ww, "MODE", "backtest")
    monkeypatch.setattr(ww, "MT5_WEDGE_EXIT_SECONDS", 100)
    monkeypatch.setattr(ww, "MT5_WEDGE_DEFER_CEILING_SECONDS", 10_000)
    monkeypatch.setattr(ww, "sdk_wedge_status", lambda: ("mt5.initialize", 150.0))
    clock = FakeClock()
    monkeypatch.setattr(ww.time, "monotonic", clock)
    exit_mock = MagicMock()

    with pytest.raises(_StopLoop):
        ww._watchdog_loop(
            exit_mock, run_lock_held=lambda: True, sleep_fn=_bounded_sleep(clock, 5),
        )

    exit_mock.assert_not_called()


def test_watchdog_force_exits_once_the_defer_ceiling_is_reached_despite_run_lock_held(monkeypatch):
    """The ceiling must fire even when RUN_LOCK stays continuously held —
    simulating a chain of queued jobs re-acquiring it back to back, which
    must NOT reset the deferral timer (finding 3: an unbounded defer is
    effectively 'never exit' on a busy terminal)."""
    monkeypatch.setattr(ww, "MODE", "backtest")
    monkeypatch.setattr(ww, "MT5_WEDGE_EXIT_SECONDS", 100)
    monkeypatch.setattr(ww, "MT5_WEDGE_CHECK_INTERVAL_SECONDS", 15)
    monkeypatch.setattr(ww, "MT5_WEDGE_DEFER_CEILING_SECONDS", 30)
    monkeypatch.setattr(ww, "sdk_wedge_status", lambda: ("mt5.initialize", 150.0))
    clock = FakeClock()
    monkeypatch.setattr(ww.time, "monotonic", clock)
    exit_mock = MagicMock()

    ww._watchdog_loop(
        exit_mock,
        run_lock_held=lambda: True,  # never frees, as if jobs chain forever
        sleep_fn=lambda s: clock.advance(s),
    )

    exit_mock.assert_called_once_with(ww.WEDGE_EXIT_CODE)


def test_watchdog_defer_ceiling_is_not_reset_by_a_worker_that_briefly_looks_unwedged(monkeypatch):
    """If the wedge clears and comes back (a different call wedges later),
    that is logically a NEW wedge — the deferral timer must restart, not
    keep accumulating from the first one."""
    monkeypatch.setattr(ww, "MODE", "backtest")
    monkeypatch.setattr(ww, "MT5_WEDGE_EXIT_SECONDS", 100)
    monkeypatch.setattr(ww, "MT5_WEDGE_CHECK_INTERVAL_SECONDS", 15)
    monkeypatch.setattr(ww, "MT5_WEDGE_DEFER_CEILING_SECONDS", 20)
    clock = FakeClock()
    monkeypatch.setattr(ww.time, "monotonic", clock)

    # Tick 1: wedged (t=15, wants_to_exit_since=15, deferred_for=0 < 20).
    # Tick 2: clears (t=30) -- resets wants_to_exit_since to None.
    # Tick 3: wedged again (t=45, wants_to_exit_since=45, deferred_for=0 < 20).
    statuses = iter([
        ("mt5.initialize", 150.0),
        None,
        ("mt5.initialize", 150.0),
    ])
    monkeypatch.setattr(ww, "sdk_wedge_status", lambda: next(statuses))
    exit_mock = MagicMock()

    with pytest.raises(StopIteration):
        ww._watchdog_loop(
            exit_mock, run_lock_held=lambda: True, sleep_fn=lambda s: clock.advance(s),
        )

    exit_mock.assert_not_called()


def test_run_lock_held_reads_the_real_backtest_handler_lock(monkeypatch):
    """_run_lock_held is the production run_lock_held used by
    start_wedge_watchdog's default — check it actually reflects RUN_LOCK."""
    from mt5api.backtest.handler import RUN_LOCK

    assert ww._run_lock_held() is False
    RUN_LOCK.acquire()
    try:
        assert ww._run_lock_held() is True
    finally:
        RUN_LOCK.release()


def test_start_wedge_watchdog_starts_a_daemon_thread(monkeypatch):
    monkeypatch.setattr(ww, "MT5_WEDGE_CHECK_INTERVAL_SECONDS", 9999)
    t = ww.start_wedge_watchdog()
    try:
        assert t.daemon is True
        assert t.name == "mt5-wedge-watchdog"
        assert t.is_alive()
    finally:
        # No clean shutdown API — it's a daemon thread parked in time.sleep()
        # for the (patched, very long) check interval; the process exiting
        # at test-suite end is what reaps it in production too.
        pass
