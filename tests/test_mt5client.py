"""Direct tests for mt5api.mt5client — the module every handler funnels
through to reach the MT5 SDK. broker_to_utc_seconds/_ms, utc_seconds_to_broker_dt,
to_dict, and build_order_request already have dedicated test files
(test_broker_time.py, test_to_dict.py, test_build_order_request.py); this file
covers everything else: the connect/reconnect state machine, the per-call
timeout+timing wrapper, the queue-depth/lock session, the Flask-facing
decorator, and the Windows-process-discovery fallback used to restart a
wedged terminal.

All MT5 SDK calls are mocked per-test — either via monkeypatch.setattr on the
MetaTrader5 stub module's attributes (auto-reverted by pytest) or by
monkeypatching mt5client's own `m` wrapper, since conftest.py does not reset
the stub's MagicMocks between tests.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import flask
import psutil
import pytest

import MetaTrader5 as mt5
import mt5api.mt5client as mc

# A throwaway Flask app just to get an app/request context for the handful
# of functions (_req_id, with_mt5) that touch flask.g / flask.jsonify. Using
# a fresh app avoids importing the whole mt5api.server module for a leaf test.
_flask_app = flask.Flask(__name__)


class FakeProc:
    """Stand-in for a psutil.Process yielded by process_iter(["pid", "name", "exe"]).

    `.info` is a property (like the real psutil.Process.info cache) so it can
    be made to raise psutil.AccessDenied / psutil.NoSuchProcess the same way
    the real thing would for a process this user can't inspect.
    """

    def __init__(self, pid, name, exe="", raise_on_info=None):
        self.pid = pid
        self._name = name
        self._exe = exe
        self._raise_on_info = raise_on_info
        self.killed = False
        self.waited_timeout = None

    @property
    def info(self):
        if self._raise_on_info is not None:
            raise self._raise_on_info
        return {"pid": self.pid, "name": self._name, "exe": self._exe}

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited_timeout = timeout


class FakeCompletedProcess:
    def __init__(self, stdout=""):
        self.stdout = stdout


def _raise_wmi_unavailable(*_args, **_kwargs):
    """Stand-in for subprocess.run when the "powershell" binary is absent —
    the real, portable way _kill_terminal falls through to the psutil path
    on this Linux test box."""
    raise OSError("no shell")


TEST_TERMINAL_DIR = r"C:\terminals\brokerX\live"


# --- _bump_depth / _drop_depth / current_queue_depth -------------------------


def test_bump_and_drop_depth_track_current_queue_depth():
    assert mc.current_queue_depth() == 0
    assert mc._bump_depth() == 1
    assert mc._bump_depth() == 2
    assert mc.current_queue_depth() == 2
    mc._drop_depth()
    assert mc.current_queue_depth() == 1
    mc._drop_depth()
    assert mc.current_queue_depth() == 0


# --- _req_id -------------------------------------------------------------


def test_req_id_outside_request_context_is_dash():
    assert mc._req_id() == "-"


def test_req_id_reads_g_inside_request_context():
    with _flask_app.test_request_context("/"):
        flask.g.req_id = "abc123"
        assert mc._req_id() == "abc123"


def test_req_id_defaults_to_dash_when_g_has_no_req_id():
    with _flask_app.test_request_context("/"):
        assert mc._req_id() == "-"


# --- load_accounts / get_first_account ------------------------------------


def test_load_accounts_returns_empty_dict_when_no_accounts_key(monkeypatch):
    monkeypatch.setattr(mc, "load_yaml_config", lambda: {})
    assert mc.load_accounts() == {}


def test_load_accounts_returns_empty_dict_when_broker_not_configured(monkeypatch):
    monkeypatch.setattr(mc, "load_yaml_config", lambda: {"accounts": {"other": {}}})
    monkeypatch.setattr(mc, "BROKER", "darwinex")
    assert mc.load_accounts() == {}


def test_load_accounts_returns_this_brokers_accounts(monkeypatch):
    accounts = {"live": {"login": 1}, "demo": {"login": 2}}
    monkeypatch.setattr(mc, "load_yaml_config", lambda: {"accounts": {"darwinex": accounts}})
    monkeypatch.setattr(mc, "BROKER", "darwinex")
    assert mc.load_accounts() == accounts


def test_get_first_account_returns_none_when_no_accounts(monkeypatch):
    monkeypatch.setattr(mc, "load_accounts", lambda: {})
    assert mc.get_first_account() is None


def test_get_first_account_prefers_configured_account(monkeypatch):
    accounts = {"live": {"login": 1}, "demo": {"login": 2}}
    monkeypatch.setattr(mc, "load_accounts", lambda: accounts)
    monkeypatch.setattr(mc, "ACCOUNT", "demo")
    assert mc.get_first_account() == {"login": 2}


def test_get_first_account_falls_back_to_first_when_account_unset(monkeypatch):
    accounts = {"live": {"login": 1}, "demo": {"login": 2}}
    monkeypatch.setattr(mc, "load_accounts", lambda: accounts)
    monkeypatch.setattr(mc, "ACCOUNT", "")
    assert mc.get_first_account() == {"login": 1}


def test_get_first_account_falls_back_to_first_when_account_not_present(monkeypatch):
    accounts = {"live": {"login": 1}, "demo": {"login": 2}}
    monkeypatch.setattr(mc, "load_accounts", lambda: accounts)
    monkeypatch.setattr(mc, "ACCOUNT", "not-there")
    assert mc.get_first_account() == {"login": 1}


# --- _run_with_timeout -----------------------------------------------------


def test_run_with_timeout_returns_the_function_result():
    assert mc._run_with_timeout(lambda: 42, timeout=5) == 42


def test_run_with_timeout_reraises_the_function_exception():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        mc._run_with_timeout(boom, timeout=5)


def test_run_with_timeout_raises_mt5timeout_when_function_wedges():
    def slow():
        time.sleep(0.2)
        return "too late"

    with pytest.raises(mc.MT5Timeout):
        mc._run_with_timeout(slow, timeout=0.05)


def test_run_with_timeout_distinguishes_none_result_from_timeout():
    """A legitimate None return must NOT look like a timeout."""
    assert mc._run_with_timeout(lambda: None, timeout=5) is None


# --- m() — the per-call timeout + timing wrapper ---------------------------


def test_m_invokes_the_underlying_call_with_the_right_args_and_propagates_return():
    def sdk_call(symbol, volume=0.0):
        return {"symbol": symbol, "volume": volume}

    result = mc.m(sdk_call, "EURUSD", volume=0.1, _timeout=5)
    assert result == {"symbol": "EURUSD", "volume": 0.1}


def test_m_routes_through_to_a_real_sdk_mock(monkeypatch):
    fake_terminal_info = MagicMock(return_value=SimpleNamespace(connected=True))
    fake_terminal_info.__name__ = "terminal_info"
    monkeypatch.setattr(mt5, "terminal_info", fake_terminal_info)

    result = mc.m(mt5.terminal_info, _timeout=5)

    fake_terminal_info.assert_called_once_with()
    assert result.connected is True


def test_m_propagates_the_underlying_exception():
    def sdk_call():
        raise KeyError("bad state")

    with pytest.raises(KeyError):
        mc.m(sdk_call, _timeout=5)


def test_m_raises_mt5timeout_when_the_call_wedges():
    def sdk_call():
        time.sleep(0.2)

    with pytest.raises(mc.MT5Timeout):
        mc.m(sdk_call, _timeout=0.05)


# --- session() — queue depth + lock ----------------------------------------


def test_session_runs_the_body_and_releases_afterwards():
    with mc.session():
        assert mc.current_queue_depth() == 1
    assert mc.current_queue_depth() == 0


def test_session_drops_depth_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with mc.session():
            raise ValueError("handler blew up")
    assert mc.current_queue_depth() == 0


def test_session_rejects_when_queue_depth_exceeds_max(monkeypatch):
    monkeypatch.setattr(mc, "MAX_QUEUE_DEPTH", 0)
    with pytest.raises(mc.QueueFull):
        with mc.session():
            pass
    assert mc.current_queue_depth() == 0


def test_session_rejects_when_lock_acquire_times_out(monkeypatch):
    class _FakeLock:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError("release should not be called — acquire failed")

    monkeypatch.setattr(mc, "_mt5_lock", _FakeLock())
    with pytest.raises(mc.QueueFull, match="could not acquire MT5 lock"):
        with mc.session():
            pass
    assert mc.current_queue_depth() == 0


# --- with_mt5() — Flask handler decorator -----------------------------------


def test_with_mt5_returns_the_handler_result_on_success():
    @mc.with_mt5
    def handler():
        return "ok", 200

    assert handler() == ("ok", 200)


def test_with_mt5_maps_queue_full_to_503():
    @mc.with_mt5
    def handler():
        raise mc.QueueFull("queue depth 25 exceeds max 20")

    with _flask_app.test_request_context("/"):
        resp, status = handler()
        assert status == 503
        assert "queue depth 25 exceeds max 20" in resp.get_json()["error"]


def test_with_mt5_maps_timeout_to_504():
    @mc.with_mt5
    def handler():
        raise mc.MT5Timeout("call timed out after 30s")

    with _flask_app.test_request_context("/"):
        resp, status = handler()
        assert status == 504
        assert "call timed out after 30s" in resp.get_json()["error"]


# --- ensure_symbol -----------------------------------------------------------


def test_ensure_symbol_returns_false_for_unknown_symbol(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: (calls.append((fn.__name__, a)), None)[1])
    assert mc.ensure_symbol("BOGUS") is False
    assert calls == [("symbol_info", ("BOGUS",))]


def test_ensure_symbol_does_not_reselect_an_already_visible_symbol(monkeypatch):
    calls = []

    def fake_m(fn, *a, **kw):
        calls.append(fn.__name__)
        if fn.__name__ == "symbol_info":
            return SimpleNamespace(visible=True)
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr(mc, "m", fake_m)
    assert mc.ensure_symbol("EURUSD") is True
    assert calls == ["symbol_info"]


def test_ensure_symbol_selects_a_hidden_symbol(monkeypatch):
    calls = []

    def fake_m(fn, *a, **kw):
        calls.append((fn.__name__, a))
        if fn.__name__ == "symbol_info":
            return SimpleNamespace(visible=False)
        return None

    monkeypatch.setattr(mc, "m", fake_m)
    assert mc.ensure_symbol("EURUSD") is True
    assert calls == [("symbol_info", ("EURUSD",)), ("symbol_select", ("EURUSD", True))]


# --- init_mt5 ----------------------------------------------------------------


def test_init_mt5_success_builds_full_kwargs(monkeypatch):
    recorded = {}

    def fake_m(fn, *a, **kw):
        recorded["fn"] = fn.__name__
        recorded["kwargs"] = kw
        return True

    monkeypatch.setattr(mc, "m", fake_m)
    assert mc.init_mt5(login="123", password="pw", server="Srv") is True
    assert recorded["fn"] == "initialize"
    assert recorded["kwargs"] == {
        "path": mc.TERMINAL_PATH,
        "login": 123,
        "password": "pw",
        "server": "Srv",
        "_timeout": mc.INIT_TIMEOUT,
    }


def test_init_mt5_omits_optional_kwargs_when_not_given(monkeypatch):
    recorded = {}
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: recorded.update(kw) or True)
    assert mc.init_mt5() is True
    assert recorded == {"path": mc.TERMINAL_PATH, "_timeout": mc.INIT_TIMEOUT}


def test_init_mt5_returns_false_and_does_not_mark_connected_on_sdk_failure(monkeypatch):
    """The SDK reporting failure must surface as a plain False — no exception,
    no state anywhere in this module claiming a successful connection.
    """
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: False)
    assert mc.init_mt5(login="1", password="p", server="s") is False


def test_init_mt5_returns_false_on_timeout_rather_than_raising(monkeypatch):
    def fake_m(fn, *a, **kw):
        raise mc.MT5Timeout("wedged")

    monkeypatch.setattr(mc, "m", fake_m)
    assert mc.init_mt5() is False


# --- ensure_initialized — reconnect / dropped-connection handling ----------


def test_ensure_initialized_reconnects_when_terminal_info_is_none(monkeypatch):
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: None)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)
    init_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.ensure_initialized() is True
    init_mock.assert_called_once_with()


def test_ensure_initialized_reconnects_when_terminal_info_raises_timeout(monkeypatch):
    def fake_m(fn, *a, **kw):
        raise mc.MT5Timeout("wedged")

    monkeypatch.setattr(mc, "m", fake_m)
    account = {"login": "1", "password": "p", "server": "s"}
    monkeypatch.setattr(mc, "get_first_account", lambda: account)
    init_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.ensure_initialized() is True
    init_mock.assert_called_once_with(**account)


def test_ensure_initialized_treats_account_info_timeout_as_not_logged_in(monkeypatch):
    def fake_m(fn, *a, **kw):
        if fn.__name__ == "terminal_info":
            return SimpleNamespace()
        if fn.__name__ == "account_info":
            raise mc.MT5Timeout("wedged")
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr(mc, "m", fake_m)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)

    # Dropped account_info -> acc=None -> "not logged in" -> no account
    # configured to log into -> the function still reports True (nothing to
    # reconnect), matching the "acc is None" and "not account" branches.
    assert mc.ensure_initialized() is True


def test_ensure_initialized_skips_reconnect_when_already_logged_in(monkeypatch):
    acc = SimpleNamespace(login=555)

    def fake_m(fn, *a, **kw):
        if fn.__name__ == "terminal_info":
            return SimpleNamespace()
        if fn.__name__ == "account_info":
            return acc
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr(mc, "m", fake_m)
    init_mock = MagicMock()
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.ensure_initialized() is True
    init_mock.assert_not_called()


@pytest.mark.parametrize("acc", [None, SimpleNamespace(login=0)], ids=["none", "login_zero"])
def test_ensure_initialized_attempts_login_when_not_logged_in(monkeypatch, acc):
    # mt5.login isn't in conftest's stub callable list (only ensure_initialized
    # uses it), so add it here — raising=False lets monkeypatch create + then
    # auto-remove a brand new attribute rather than requiring it pre-exist.
    fake_login_fn = MagicMock()
    fake_login_fn.__name__ = "login"
    monkeypatch.setattr(mt5, "login", fake_login_fn, raising=False)
    account = {"login": "1", "password": "pw", "server": "srv"}

    def fake_m(fn, *a, **kw):
        if fn.__name__ == "terminal_info":
            return SimpleNamespace()
        if fn.__name__ == "account_info":
            return acc
        if fn.__name__ == "login":
            assert kw == {
                "login": 1,
                "password": "pw",
                "server": "srv",
                "_timeout": mc.INIT_TIMEOUT,
            }
            return True
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr(mc, "m", fake_m)
    monkeypatch.setattr(mc, "get_first_account", lambda: account)

    assert mc.ensure_initialized() is True


def test_ensure_initialized_returns_true_when_terminal_up_but_no_account_configured(monkeypatch):
    def fake_m(fn, *a, **kw):
        if fn.__name__ == "terminal_info":
            return SimpleNamespace()
        if fn.__name__ == "account_info":
            return SimpleNamespace(login=0)
        raise AssertionError(f"unexpected call: {fn.__name__}")

    monkeypatch.setattr(mc, "m", fake_m)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)

    assert mc.ensure_initialized() is True


def test_ensure_initialized_login_timeout_returns_false(monkeypatch):
    fake_login_fn = MagicMock()
    fake_login_fn.__name__ = "login"
    monkeypatch.setattr(mt5, "login", fake_login_fn, raising=False)

    def fake_m(fn, *a, **kw):
        if fn.__name__ == "terminal_info":
            return SimpleNamespace()
        if fn.__name__ == "account_info":
            return SimpleNamespace(login=0)
        if fn.__name__ == "login":
            raise mc.MT5Timeout("wedged")
        raise AssertionError(f"unexpected call: {fn.__name__}")

    account = {"login": "1", "password": "p", "server": "s"}
    monkeypatch.setattr(mc, "m", fake_m)
    monkeypatch.setattr(mc, "get_first_account", lambda: account)

    assert mc.ensure_initialized() is False


# --- _kill_terminal — WMI path + psutil fallback ----------------------------


def test_kill_terminal_wmi_filter_narrows_to_the_configured_account(monkeypatch):
    """When ACCOUNT is configured, the WMI filter must scope to this
    account+instance's own terminal — not every terminal for the broker."""
    monkeypatch.setattr(mc, "ACCOUNT", "live")
    monkeypatch.setattr(mc, "BROKER", "darwinex")
    monkeypatch.setattr(mc, "INSTANCE", "a")
    captured = {}

    def fake_run(cmd, **kw):
        captured["ps_cmd"] = cmd[-1]
        return FakeCompletedProcess("NONE\n")

    monkeypatch.setattr(mc.subprocess, "run", fake_run)

    assert mc._kill_terminal() is False
    assert r"*\darwinex\live\a\*" in captured["ps_cmd"]


def test_kill_terminal_wmi_kill_reports_success(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **kw: FakeCompletedProcess("KILLED\n"))
    sleep_calls = []
    monkeypatch.setattr(mc.time, "sleep", lambda s: sleep_calls.append(s))

    assert mc._kill_terminal() is True
    assert sleep_calls == [2]


def test_kill_terminal_wmi_reports_no_process_found(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **kw: FakeCompletedProcess("NONE\n"))

    assert mc._kill_terminal() is False


def test_kill_terminal_falls_back_to_psutil_when_wmi_raises(monkeypatch):
    def raising_run(*a, **kw):
        raise FileNotFoundError("powershell not found")

    monkeypatch.setattr(mc.subprocess, "run", raising_run)
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    proc = FakeProc(pid=42, name="terminal64.exe", exe=TEST_TERMINAL_DIR + r"\terminal64.exe")
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([proc]))

    assert mc._kill_terminal() is True
    assert proc.killed is True
    assert proc.waited_timeout == 10


def test_kill_terminal_falls_back_to_psutil_when_wmi_output_is_unrecognized(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **kw: FakeCompletedProcess("garbage\n"))
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    proc = FakeProc(pid=7, name="terminal64.exe", exe=TEST_TERMINAL_DIR + r"\terminal64.exe")
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([proc]))

    assert mc._kill_terminal() is True
    assert proc.killed is True


def test_kill_terminal_psutil_skips_process_with_a_different_name(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", _raise_wmi_unavailable)
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    other = FakeProc(pid=1, name="notepad.exe", exe=r"C:\windows\notepad.exe")
    ours = FakeProc(pid=2, name="terminal64.exe", exe=TEST_TERMINAL_DIR + r"\terminal64.exe")
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([other, ours]))

    assert mc._kill_terminal() is True
    assert other.killed is False
    assert ours.killed is True


def test_kill_terminal_psutil_skips_terminal_from_a_different_broker_directory(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", _raise_wmi_unavailable)
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    other_exe = r"C:\terminals\brokerY\live\terminal64.exe"
    other_broker = FakeProc(pid=1, name="terminal64.exe", exe=other_exe)
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([other_broker]))

    assert mc._kill_terminal() is False
    assert other_broker.killed is False


def test_kill_terminal_psutil_skips_access_denied_and_no_such_process(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", _raise_wmi_unavailable)
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    denied = FakeProc(pid=1, name="terminal64.exe", raise_on_info=psutil.AccessDenied())
    gone = FakeProc(pid=2, name="terminal64.exe", raise_on_info=psutil.NoSuchProcess(2))
    ours = FakeProc(pid=3, name="terminal64.exe", exe=TEST_TERMINAL_DIR + r"\terminal64.exe")
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([denied, gone, ours]))

    assert mc._kill_terminal() is True
    assert ours.killed is True


def test_kill_terminal_returns_false_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(mc.subprocess, "run", _raise_wmi_unavailable)
    monkeypatch.setattr(mc, "TERMINAL_DIR", TEST_TERMINAL_DIR)
    monkeypatch.setattr(mc.psutil, "process_iter", lambda attrs=None: iter([]))

    assert mc._kill_terminal() is False


# --- _wait_for_journal -------------------------------------------------------


def test_wait_for_journal_returns_true_once_the_marker_appears(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    journal = tmp_path / "20260806.log"
    journal.write_bytes("terminal started for account 123".encode("utf-16-le"))

    assert mc._wait_for_journal(str(journal), 0, max_attempts=3) is True


def test_wait_for_journal_gives_up_after_max_attempts(monkeypatch, tmp_path):
    sleeps = []
    monkeypatch.setattr(mc.time, "sleep", lambda s: sleeps.append(s))
    journal = tmp_path / "20260806.log"
    journal.write_bytes("still booting...".encode("utf-16-le"))

    assert mc._wait_for_journal(str(journal), 0, max_attempts=2) is False
    assert sleeps == [5, 5]


def test_wait_for_journal_waits_out_a_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    missing = tmp_path / "never-shows-up.log"

    assert mc._wait_for_journal(str(missing), 0, max_attempts=2) is False


def test_wait_for_journal_swallows_os_error_and_keeps_polling(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    unreadable = tmp_path / "not_a_file.log"
    unreadable.mkdir()  # opening a directory as a file raises OSError

    assert mc._wait_for_journal(str(unreadable), 0, max_attempts=2) is False


def test_wait_for_journal_respects_the_offset(monkeypatch, tmp_path):
    """Content before `offset` must not be re-scanned — otherwise a marker
    left over from the PREVIOUS terminal session would report a false ready.
    """
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    journal = tmp_path / "20260806.log"
    prefix = "terminal started for OLD SESSION".encode("utf-16-le")
    journal.write_bytes(prefix)

    assert mc._wait_for_journal(str(journal), len(prefix), max_attempts=1) is False


# --- restart_terminal — full kill/relaunch/reconnect orchestration --------


def test_restart_terminal_gives_up_when_journal_never_appears(monkeypatch):
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: True)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: True)
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(mc, "_wait_for_journal", lambda *a, **kw: False)
    init_mock = MagicMock()
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.restart_terminal() is False
    init_mock.assert_not_called()


def test_restart_terminal_relaunches_with_the_configured_account(monkeypatch):
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: True)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: True)
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(mc, "_wait_for_journal", lambda *a, **kw: True)
    account = {"login": "1", "password": "pw", "server": "srv"}
    monkeypatch.setattr(mc, "get_first_account", lambda: account)
    init_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.restart_terminal() is True
    init_mock.assert_called_once_with(**account)


def test_restart_terminal_seeks_past_pre_existing_journal_content(monkeypatch, tmp_path):
    """When today's journal file already has content (terminal was restarted
    earlier today), the offset passed to _wait_for_journal must skip past it
    — otherwise a leftover "started for" line from the OLD boot would report
    the NEW terminal ready before it actually is.
    """
    today = mc.date.today().strftime("%Y%m%d")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    journal = logs_dir / f"{today}.log"
    old_content = "started for OLD SESSION".encode("utf-16-le")
    journal.write_bytes(old_content)

    monkeypatch.setattr(mc, "TERMINAL_DIR", str(tmp_path))
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: True)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: True)
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    captured_offset = {}

    def fake_wait_for_journal(journal_log, offset, **kw):
        captured_offset["offset"] = offset
        return True

    monkeypatch.setattr(mc, "_wait_for_journal", fake_wait_for_journal)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)
    monkeypatch.setattr(mc, "init_mt5", lambda *a, **kw: True)

    assert mc.restart_terminal() is True
    assert captured_offset["offset"] == len(old_content)


def test_restart_terminal_relaunches_without_a_configured_account(monkeypatch):
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: True)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: False)  # no process found — still proceeds
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(mc, "_wait_for_journal", lambda *a, **kw: True)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)
    init_mock = MagicMock(return_value=True)
    monkeypatch.setattr(mc, "init_mt5", init_mock)

    assert mc.restart_terminal() is True
    init_mock.assert_called_once_with()


def test_restart_terminal_reports_failure_when_reconnect_fails(monkeypatch):
    monkeypatch.setattr(mc, "m", lambda fn, *a, **kw: True)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: True)
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(mc, "_wait_for_journal", lambda *a, **kw: True)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)
    monkeypatch.setattr(mc, "init_mt5", lambda *a, **kw: False)

    assert mc.restart_terminal() is False


def test_restart_terminal_survives_a_shutdown_timeout(monkeypatch):
    def fake_m(fn, *a, **kw):
        raise mc.MT5Timeout("wedged")

    monkeypatch.setattr(mc, "m", fake_m)
    monkeypatch.setattr(mc, "_kill_terminal", lambda: True)
    monkeypatch.setattr(mc.subprocess, "Popen", MagicMock())
    monkeypatch.setattr(mc, "_wait_for_journal", lambda *a, **kw: True)
    monkeypatch.setattr(mc, "get_first_account", lambda: None)
    monkeypatch.setattr(mc, "init_mt5", lambda *a, **kw: True)

    assert mc.restart_terminal() is True
