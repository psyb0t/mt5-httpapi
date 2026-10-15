"""Tests for mt5api.monitor — the background daemon thread that is the only
thing standing between a wedged/crashed MT5 terminal and a silently dead API.
It decides when to log, when to declare the terminal dead, and when to pull
the trigger on restart_terminal(); a bug here means either a false restart
storm or, worse, a dead terminal nobody ever restarts.

_monitor_loop() is a `while True` — every test breaks it deterministically by
monkeypatching time.sleep to raise a sentinel exception after a fixed number
of iterations, per the standard pattern for testing infinite loops. No test
in this file performs a real sleep or runs unbounded.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import mt5api.mt5client as mc
import mt5api.monitor as monitor


class _StopLoop(Exception):
    """Raised by the faked time.sleep once the scripted iterations are
    exhausted, to break _monitor_loop's `while True` from the outside."""


def _drive_loop(monkeypatch, iterations, extra_setup=None):
    """Run monitor._monitor_loop for exactly len(iterations) passes, then
    stop it via _StopLoop.

    `iterations` is a list of dicts mapping an MT5 fn name ("terminal_info",
    "account_info") to either a return value or an Exception instance the
    fake `m` should raise for that call. A new "pass" begins every time
    `terminal_info` is called, matching _monitor_loop's own call order.

    Returns the MagicMock that replaced monitor.log, for call assertions. The
    keyword arguments of every session() call are on its `session_calls`.
    """
    state = {"idx": -1}
    session_calls = []

    def fake_m(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "?")
        if name == "terminal_info":
            state["idx"] += 1
        behavior = iterations[state["idx"]].get(name)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    sleep_state = {"count": 0}

    def fake_sleep(_secs):
        sleep_state["count"] += 1
        if sleep_state["count"] > len(iterations):
            raise _StopLoop()

    @contextmanager
    def noop_session(**kwargs):
        session_calls.append(kwargs)
        yield

    monkeypatch.setattr(monitor, "m", fake_m)
    monkeypatch.setattr(monitor.time, "sleep", fake_sleep)
    monkeypatch.setattr(monitor, "session", noop_session)
    if extra_setup:
        extra_setup(monkeypatch)

    fake_log = MagicMock()
    fake_log.session_calls = session_calls
    monkeypatch.setattr(monitor, "log", fake_log)

    with pytest.raises(_StopLoop):
        monitor._monitor_loop()

    return fake_log


def _error_messages(fake_log):
    return [c.args[0] for c in fake_log.error.call_args_list if c.args]


def _info_messages(fake_log):
    return [c.args[0] for c in fake_log.info.call_args_list if c.args]


# --- _check_ini_autotrading --------------------------------------------------


def test_check_ini_autotrading_missing_file_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "INI_FILE", str(tmp_path / "missing.ini"))
    assert monitor._check_ini_autotrading() is False


def test_check_ini_autotrading_true_when_enabled(tmp_path, monkeypatch):
    ini = tmp_path / "mt5start.ini"
    ini.write_text("[Common]\nAutoTrading=1\n")
    monkeypatch.setattr(monitor, "INI_FILE", str(ini))
    assert monitor._check_ini_autotrading() is True


def test_check_ini_autotrading_false_when_disabled(tmp_path, monkeypatch):
    ini = tmp_path / "mt5start.ini"
    ini.write_text("[Common]\nAutoTrading=0\n")
    monkeypatch.setattr(monitor, "INI_FILE", str(ini))
    assert monitor._check_ini_autotrading() is False


def test_check_ini_autotrading_ignores_key_outside_common_section(tmp_path, monkeypatch):
    ini = tmp_path / "mt5start.ini"
    ini.write_text("[Expert]\nAutoTrading=1\n[Common]\nOtherKey=1\n")
    monkeypatch.setattr(monitor, "INI_FILE", str(ini))
    assert monitor._check_ini_autotrading() is False


def test_check_ini_autotrading_section_and_key_are_case_insensitive(tmp_path, monkeypatch):
    ini = tmp_path / "mt5start.ini"
    ini.write_text("[COMMON]\nAUTOTRADING=1\n")
    monkeypatch.setattr(monitor, "INI_FILE", str(ini))
    assert monitor._check_ini_autotrading() is True


def test_check_ini_autotrading_swallows_os_error(tmp_path, monkeypatch):
    a_directory = tmp_path / "mt5start.ini"
    a_directory.mkdir()  # opening a directory as a file raises OSError
    monkeypatch.setattr(monitor, "INI_FILE", str(a_directory))
    assert monitor._check_ini_autotrading() is False


# --- _monitor_loop — dead terminal / restart threshold ----------------------


def test_monitor_loop_logs_dead_terminal_without_restarting_below_threshold(monkeypatch):
    n = monitor.DEAD_CHECKS_BEFORE_RESTART - 1
    iterations = [{"terminal_info": None} for _ in range(n)]
    restart_mock = MagicMock()
    monkeypatch.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, iterations)

    restart_mock.assert_not_called()
    dead_msg = "!!! TERMINAL NOT RUNNING !!! (%d/%d before restart)"
    assert _error_messages(fake_log).count(dead_msg) == n


def test_monitor_loop_restarts_terminal_after_dead_threshold_and_logs_success(monkeypatch):
    n = monitor.DEAD_CHECKS_BEFORE_RESTART
    iterations = [{"terminal_info": None} for _ in range(n)]
    restart_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, iterations)

    restart_mock.assert_called_once()
    assert "Auto-restart succeeded." in _info_messages(fake_log)


def test_monitor_loop_restart_failure_logs_and_keeps_trying(monkeypatch):
    n = monitor.DEAD_CHECKS_BEFORE_RESTART
    iterations = [{"terminal_info": None} for _ in range(n)]
    restart_mock = MagicMock(return_value=False)
    monkeypatch.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, iterations)

    restart_mock.assert_called_once()
    assert "Auto-restart FAILED, will keep trying." in _error_messages(fake_log)


def test_monitor_loop_treats_terminal_info_timeout_as_a_dropped_connection(monkeypatch):
    """A terminal_info call that outright raises MT5Timeout must be treated
    exactly like one that returns None — both mean the terminal is dead."""
    iterations = [{"terminal_info": monitor.MT5Timeout("wedged")}]
    restart_mock = MagicMock()
    monkeypatch.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, iterations)

    dead_msg = "!!! TERMINAL NOT RUNNING !!! (%d/%d before restart)"
    assert dead_msg in _error_messages(fake_log)
    restart_mock.assert_not_called()


def test_monitor_loop_treats_non_timeout_terminal_info_error_as_dropped_connection(monkeypatch):
    """The inner probe also has a bare `except Exception` fallback (any SDK
    error, not just a wedge) — it must be just as fatal as MT5Timeout."""
    iterations = [{"terminal_info": RuntimeError("SDK blew up")}]
    restart_mock = MagicMock()
    monkeypatch.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, iterations)

    dead_msg = "!!! TERMINAL NOT RUNNING !!! (%d/%d before restart)"
    assert dead_msg in _error_messages(fake_log)
    restart_mock.assert_not_called()


def test_monitor_loop_treats_a_non_timeout_account_info_error_as_not_logged_in(monkeypatch):
    info = SimpleNamespace(trade_allowed=True)
    iterations = [{"terminal_info": info, "account_info": RuntimeError("SDK blew up")}]

    fake_log = _drive_loop(monkeypatch, iterations)

    assert "!!! NOT LOGGED IN !!!" in _error_messages(fake_log)


# --- _monitor_loop — algo trading disabled / ini check ----------------------


def test_monitor_loop_flags_disabled_algo_trading_with_broken_ini(monkeypatch):
    info = SimpleNamespace(trade_allowed=False)
    iterations = [{"terminal_info": info, "account_info": None}]

    def extra(mp):
        mp.setattr(monitor, "_check_ini_autotrading", lambda: False)

    fake_log = _drive_loop(monkeypatch, iterations, extra_setup=extra)

    messages = _error_messages(fake_log)
    assert "!!! ALGO TRADING DISABLED IN TERMINAL !!!" in messages
    assert "!!! INI FILE MISSING AutoTrading=1 — config is broken !!!" in messages


def test_monitor_loop_disabled_algo_trading_with_healthy_ini_skips_extra_warning(monkeypatch):
    info = SimpleNamespace(trade_allowed=False)
    iterations = [{"terminal_info": info, "account_info": None}]

    def extra(mp):
        mp.setattr(monitor, "_check_ini_autotrading", lambda: True)

    fake_log = _drive_loop(monkeypatch, iterations, extra_setup=extra)

    messages = _error_messages(fake_log)
    assert "!!! ALGO TRADING DISABLED IN TERMINAL !!!" in messages
    assert "!!! INI FILE MISSING AutoTrading=1 — config is broken !!!" not in messages


def test_monitor_loop_logs_state_transitions_only_once_across_consecutive_passes(monkeypatch):
    """Algo-enabled and logged-in are both edge-triggered logs — they must
    fire once on the transition, not on every healthy pass."""
    info = SimpleNamespace(trade_allowed=True)
    acc = SimpleNamespace(login=777, server="Broker-Live")
    iterations = [
        {"terminal_info": info, "account_info": acc},
        {"terminal_info": info, "account_info": acc},
    ]

    fake_log = _drive_loop(monkeypatch, iterations)

    info_calls = fake_log.info.call_args_list
    enabled_calls = [c for c in info_calls if c.args[:1] == ("Algo trading is enabled.",)]
    login_calls = [c for c in info_calls if c.args[:1] == ("Logged in as %s on %s",)]
    assert len(enabled_calls) == 1
    assert len(login_calls) == 1
    assert login_calls[0].args[1:] == (777, "Broker-Live")


# --- _monitor_loop — login state ---------------------------------------------


def test_monitor_loop_flags_not_logged_in_when_account_info_is_none(monkeypatch):
    info = SimpleNamespace(trade_allowed=True)
    iterations = [{"terminal_info": info, "account_info": None}]

    fake_log = _drive_loop(monkeypatch, iterations)

    assert "!!! NOT LOGGED IN !!!" in _error_messages(fake_log)


def test_monitor_loop_flags_not_logged_in_when_login_is_zero(monkeypatch):
    info = SimpleNamespace(trade_allowed=True)
    acc = SimpleNamespace(login=0, server="x")
    iterations = [{"terminal_info": info, "account_info": acc}]

    fake_log = _drive_loop(monkeypatch, iterations)

    assert "!!! NOT LOGGED IN !!!" in _error_messages(fake_log)


def test_monitor_loop_treats_account_info_timeout_as_not_logged_in(monkeypatch):
    info = SimpleNamespace(trade_allowed=True)
    iterations = [{"terminal_info": info, "account_info": monitor.MT5Timeout("wedged")}]

    fake_log = _drive_loop(monkeypatch, iterations)

    assert "!!! NOT LOGGED IN !!!" in _error_messages(fake_log)


# --- _monitor_loop — session-level backpressure/failure ---------------------


def test_monitor_loop_survives_a_session_failure_and_keeps_looping(monkeypatch):
    """session() itself can raise (queue-depth backpressure, lock-acquire
    timeout) outside the inner try/excepts — the loop must log and retry,
    never crash the monitor thread."""

    @contextmanager
    def raising_session():
        raise mc.QueueFull("queue depth 25 exceeds max 20")
        yield  # pragma: no cover - unreachable, keeps this a generator function

    def extra(mp):
        mp.setattr(monitor, "session", raising_session)

    fake_log = _drive_loop(monkeypatch, [{}], extra_setup=extra)

    assert fake_log.warning.call_count == 1
    warn_call = fake_log.warning.call_args
    assert warn_call.args[0] == "Monitor session failed: %s"


def test_monitor_loop_takes_its_session_past_the_queue_cap(monkeypatch):
    info = SimpleNamespace(trade_allowed=True)
    acc = SimpleNamespace(login=1, server="S")

    fake_log = _drive_loop(monkeypatch, [{"terminal_info": info, "account_info": acc}])

    assert fake_log.session_calls == [{"bypass_queue_cap": True}]


def _session_script(lock_timeouts):
    """A session() stand-in that raises MT5LockTimeout for the first
    `lock_timeouts` calls and then yields normally."""
    calls = {"n": 0}

    @contextmanager
    def scripted_session(**_kwargs):
        calls["n"] += 1
        if calls["n"] <= lock_timeouts:
            raise mc.MT5LockTimeout("could not acquire MT5 lock")
        yield

    return scripted_session


def test_monitor_loop_counts_a_lock_timeout_as_a_dead_check(monkeypatch):
    n = monitor.DEAD_CHECKS_BEFORE_RESTART - 1
    kill_mock = MagicMock(return_value=True)
    restart_mock = MagicMock()

    def extra(mp):
        mp.setattr(monitor, "session", _session_script(n))
        mp.setattr(mc, "_kill_terminal", kill_mock)
        mp.setattr(mc, "restart_terminal", restart_mock)

    fake_log = _drive_loop(monkeypatch, [{} for _ in range(n)], extra_setup=extra)

    lock_msg = "!!! MT5 LOCK HELD FOR OVER %ds !!! (%d/%d before restart)"
    assert _error_messages(fake_log).count(lock_msg) == n
    kill_mock.assert_not_called()
    restart_mock.assert_not_called()
    fake_log.warning.assert_not_called()


def test_monitor_loop_kills_the_terminal_once_the_lock_stays_held_and_restarts_it_next_check(monkeypatch):
    n = monitor.DEAD_CHECKS_BEFORE_RESTART
    kill_mock = MagicMock(return_value=True)
    restart_mock = MagicMock(return_value=True)

    def extra(mp):
        mp.setattr(monitor, "session", _session_script(n))
        mp.setattr(mc, "_kill_terminal", kill_mock)
        mp.setattr(mc, "restart_terminal", restart_mock)

    iterations = [{} for _ in range(n)] + [{"terminal_info": None}]
    fake_log = _drive_loop(monkeypatch, iterations, extra_setup=extra)

    kill_mock.assert_called_once()
    restart_mock.assert_called_once()
    assert "Terminal killed; the next check restarts it." in _info_messages(fake_log)


def test_monitor_loop_recovers_through_the_real_session_when_a_caller_holds_the_lock(monkeypatch):
    """With the real session() and a real held lock: client backpressure is
    over the cap, the lock never frees, and the monitor still counts the
    checks, kills the terminal, and restarts it once the kill frees the lock."""
    n = monitor.DEAD_CHECKS_BEFORE_RESTART
    restart_mock = MagicMock(return_value=True)

    def kill_frees_the_lock():
        mc._mt5_lock.release()
        return True

    kill_mock = MagicMock(side_effect=kill_frees_the_lock)

    def extra(mp):
        mp.setattr(monitor, "session", mc.session)
        mp.setattr(mc, "SESSION_ACQUIRE_TIMEOUT", 0.05)
        mp.setattr(mc, "MAX_QUEUE_DEPTH", 0)
        mp.setattr(mc, "_kill_terminal", kill_mock)
        mp.setattr(mc, "restart_terminal", restart_mock)

    assert mc._mt5_lock.acquire(timeout=1)
    try:
        iterations = [{} for _ in range(n)] + [{"terminal_info": None}]
        _drive_loop(monkeypatch, iterations, extra_setup=extra)
    finally:
        if mc._mt5_lock.locked():
            mc._mt5_lock.release()

    kill_mock.assert_called_once()
    restart_mock.assert_called_once()
    assert mc.current_queue_depth() == 0


# --- start_monitor ------------------------------------------------------------


def test_start_monitor_spawns_a_named_daemon_thread(monkeypatch):
    captured = {}

    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            captured["target"] = target
            captured["daemon"] = daemon
            captured["name"] = name

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(monitor.threading, "Thread", FakeThread)
    fake_log = MagicMock()
    monkeypatch.setattr(monitor, "log", fake_log)

    monitor.start_monitor()

    assert captured["target"] is monitor._monitor_loop
    assert captured["daemon"] is True
    assert captured["name"] == "mt5-monitor"
    assert captured["started"] is True
    fake_log.info.assert_called_once()
    assert fake_log.info.call_args.args[0] == "Health monitor started (check every %ds)."
    assert fake_log.info.call_args.args[1] == monitor.CHECK_INTERVAL


# --- the terminal generation watcher ---------------------------------------------


@pytest.fixture
def watcher(monkeypatch):
    """One _generation_step at a time against a scripted terminal process,
    a recorded SDK reconnect and a recorded re-apply."""
    import threading

    from mt5api.backtest import handler as backtest
    from mt5api.chartctl import terminal_pid
    from mt5api.chartctl import webrequest_apply as wa

    state = {"generation": (42, 100.0), "reconnect": True, "reconnects": 0,
             "stale": True, "pending": False, "reapplies": [], "sessions": []}

    def ensure_initialized():
        state["reconnects"] += 1
        if isinstance(state["reconnect"], Exception):
            raise state["reconnect"]
        return state["reconnect"]

    @contextmanager
    def fake_session(**kwargs):
        state["sessions"].append(kwargs)
        yield

    monkeypatch.setattr(terminal_pid, "current_generation", lambda: state["generation"])
    monkeypatch.setattr(mc, "ensure_initialized", ensure_initialized)
    monkeypatch.setattr(monitor, "session", fake_session)
    monkeypatch.setattr(wa, "is_stale", lambda gen: state["stale"])
    monkeypatch.setattr(wa, "reapply_pending", lambda: state["pending"])
    monkeypatch.setattr(wa, "gui_settle_seconds", lambda: 25)
    monkeypatch.setattr(wa, "reapply_in_background",
                        lambda delay, reason: state["reapplies"].append((delay, reason)))
    monkeypatch.setattr(backtest, "RUN_LOCK", threading.Lock())
    fake_log = MagicMock()
    monkeypatch.setattr(monitor, "log", fake_log)
    state["log"] = fake_log
    state["run_lock"] = backtest.RUN_LOCK
    return state


def test_a_new_terminal_process_reconnects_the_sdk_then_reapplies(watcher):
    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["reconnects"] == 1
    assert watcher["sessions"] == [{"bypass_queue_cap": True}]
    assert watcher["reapplies"] == [(25, "new terminal process")]


def test_the_same_process_again_does_nothing(watcher):
    monitor._generation_step({"last_seen": (42, 100.0)})

    assert watcher["reconnects"] == 0
    assert watcher["reapplies"] == []


def test_a_terminal_that_is_not_running_does_nothing(watcher):
    watcher["generation"] = None

    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["reconnects"] == 0
    assert watcher["reapplies"] == []


def test_a_new_process_whose_list_is_not_stale_only_reconnects(watcher):
    watcher["stale"] = False  # no stored list, or already applied to this process

    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["reconnects"] == 1
    assert watcher["reapplies"] == []


def test_a_pending_reapply_is_not_doubled(watcher):
    watcher["pending"] = True

    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["reconnects"] == 1
    assert watcher["reapplies"] == []


def test_the_first_sighting_after_api_start_is_left_to_the_start_up_path(watcher):
    seen = {"last_seen": None}

    monitor._generation_step(seen)

    assert seen["last_seen"] == (42, 100.0)
    assert watcher["reconnects"] == 0 and watcher["reapplies"] == []
    assert watcher["log"].warning.call_count == 0


def test_a_restart_the_api_is_making_is_left_to_restart_terminal(watcher, monkeypatch):
    seen = {"last_seen": (41, 50.0)}
    mc.RESTART_IN_PROGRESS.set()
    try:
        monitor._generation_step(seen)
    finally:
        mc.RESTART_IN_PROGRESS.clear()

    assert watcher["reconnects"] == 0 and watcher["reapplies"] == []
    assert seen["last_seen"] == (41, 50.0)


def test_a_lock_held_by_a_long_restart_is_a_warning_not_a_traceback(watcher, monkeypatch):
    @contextmanager
    def held(**_kwargs):
        raise mc.MT5LockTimeout("held")
        yield  # pragma: no cover

    monkeypatch.setattr(monitor, "session", held)

    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["log"].exception.call_count == 0
    assert watcher["reapplies"] == [(25, "new terminal process")]


@pytest.mark.parametrize("reconnect", [False, RuntimeError("sdk wedged")])
def test_a_failed_reconnect_still_reapplies(watcher, reconnect):
    watcher["reconnect"] = reconnect

    monitor._generation_step({"last_seen": (41, 50.0)})

    assert watcher["reapplies"] == [(25, "new terminal process")]


def test_a_backtest_process_is_neither_acted_on_nor_recorded(watcher):
    seen = {"last_seen": (41, 50.0)}
    watcher["run_lock"].acquire()
    try:
        monitor._generation_step(seen)
    finally:
        watcher["run_lock"].release()

    assert watcher["reconnects"] == 0 and watcher["reapplies"] == []
    assert seen["last_seen"] == (41, 50.0)  # seen as new once the run is over

    monitor._generation_step(seen)
    assert watcher["reapplies"] == [(25, "new terminal process")]


# --- the WebRequest stale alert -----------------------------------------------------


@pytest.fixture
def stale(monkeypatch):
    from mt5api.chartctl import terminal_pid
    from mt5api.chartctl import webrequest_apply as wa

    state = {"stale": True}
    monkeypatch.setattr(wa, "available", lambda: True)
    monkeypatch.setattr(terminal_pid, "last_generation", lambda: (42, 100.0))
    monkeypatch.setattr(wa, "is_stale", lambda gen: state["stale"])
    monkeypatch.setattr(wa, "load_status",
                        lambda: {"status": "FAIL", "code": "terminal_busy"})
    fake_log = MagicMock()
    monkeypatch.setattr(monitor, "log", fake_log)
    state["log"] = fake_log
    return state


def test_a_list_stale_for_four_minutes_is_not_an_error_yet(stale):
    check = {}
    monitor._webrequest_stale_check(check, 0.0)
    monitor._webrequest_stale_check(check, 240.0)

    assert _error_messages(stale["log"]) == []


def test_a_list_stale_past_five_minutes_is_an_error_on_every_pass(stale):
    check = {}
    for now in (0.0, 360.0, 420.0):
        monitor._webrequest_stale_check(check, now)

    errors = _error_messages(stale["log"])
    assert len(errors) == 2
    assert all("WEBREQUEST LIST NOT RESTORED" in e for e in errors)


def test_a_restored_list_logs_once_and_resets(stale):
    check = {}
    monitor._webrequest_stale_check(check, 0.0)
    monitor._webrequest_stale_check(check, 360.0)
    stale["stale"] = False
    monitor._webrequest_stale_check(check, 420.0)
    monitor._webrequest_stale_check(check, 480.0)

    restored = [m for m in _info_messages(stale["log"]) if "restored" in m]
    assert len(restored) == 1
    assert check == {"since": None, "alerted": False}


def test_a_health_pass_caches_terminal_info_for_lock_free_readers(monkeypatch):
    info = SimpleNamespace(trade_allowed=True, build=6230)
    monkeypatch.setattr(mc, "LAST_TERMINAL_INFO", None)

    _drive_loop(monkeypatch, [{"terminal_info": info,
                               "account_info": SimpleNamespace(login=1, server="Fake-Demo")}])

    assert mc.LAST_TERMINAL_INFO is info
