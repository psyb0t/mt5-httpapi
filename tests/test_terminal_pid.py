"""The terminal's pid, found by its exact executable path (chartctl/terminal_pid.py).

psutil and the WMI fallback are faked: these tests pin which process counts
as this terminal's, not Windows itself.
"""
from __future__ import annotations

import pytest

from mt5api.chartctl import terminal_pid as tp

OURS = r"C:\Users\Docker\Desktop\Shared\terminals\testbroker\acct1\default\terminal64.exe"


class _Proc:
    def __init__(self, pid, name, exe, created=100.0):
        self.pid = pid
        self.info = {"name": name, "exe": exe, "create_time": created}


def _processes(monkeypatch, procs):
    monkeypatch.setattr(tp.psutil, "process_iter", lambda attrs=None: iter(procs))


def _no_wmi(monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("WMI must not run when psutil can read every path")

    monkeypatch.setattr(tp.subprocess, "run", refuse)


def test_the_one_process_from_this_path_is_found(monkeypatch):
    _processes(monkeypatch, [_Proc(1, "notepad.exe", r"C:\Windows\notepad.exe"),
                             _Proc(42, "terminal64.exe", OURS, created=123.5)])
    _no_wmi(monkeypatch)

    assert tp.find_terminal(OURS) == (42, 123.5)


def test_the_host_share_spelling_of_the_same_path_matches(monkeypatch):
    on_share = r"\\host.lan\Data\terminals\testbroker\acct1\default\terminal64.exe"
    _processes(monkeypatch, [_Proc(42, "terminal64.exe", on_share)])
    _no_wmi(monkeypatch)

    assert tp.find_terminal(OURS)[0] == 42


@pytest.mark.parametrize(
    "other",
    [
        # another instance of the same account: the old no-account wildcard hit it
        r"C:\Users\Docker\Desktop\Shared\terminals\testbroker\acct1\second\terminal64.exe",
        # a sibling directory whose name starts with ours: the old substring match hit it
        r"C:\Users\Docker\Desktop\Shared\terminals\testbroker\acct1\default2\terminal64.exe",
        # another account of the broker
        r"C:\Users\Docker\Desktop\Shared\terminals\testbroker\acct2\default\terminal64.exe",
    ],
)
def test_a_terminal_from_any_other_path_is_not_ours(monkeypatch, other):
    _processes(monkeypatch, [_Proc(7, "terminal64.exe", other)])
    _no_wmi(monkeypatch)

    with pytest.raises(tp.TerminalPidError) as err:
        tp.find_terminal(OURS)
    assert err.value.code == "pid_not_found"


def test_two_processes_from_this_path_are_ambiguous(monkeypatch):
    _processes(monkeypatch, [_Proc(1, "terminal64.exe", OURS), _Proc(2, "terminal64.exe", OURS)])
    _no_wmi(monkeypatch)

    with pytest.raises(tp.TerminalPidError) as err:
        tp.find_terminal(OURS)
    assert err.value.code == "pid_ambiguous"
    assert err.value.diag == {"pids": [1, 2]}


def test_the_case_of_the_path_does_not_matter(monkeypatch):
    _processes(monkeypatch, [_Proc(42, "TERMINAL64.EXE", OURS.upper())])
    _no_wmi(monkeypatch)

    assert tp.find_terminal(OURS)[0] == 42


def test_wmi_runs_only_when_psutil_cannot_read_a_path(monkeypatch):
    _processes(monkeypatch, [_Proc(42, "terminal64.exe", None)])  # AccessDenied: exe is None
    calls = []

    class _Result:
        stdout = f"42|{OURS}\n17|C:\\elsewhere\\terminal64.exe\n"

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(tp.subprocess, "run", fake_run)
    monkeypatch.setattr(tp, "_create_time", lambda pid: 55.0)

    assert tp.find_terminal(OURS) == (42, 55.0)
    assert len(calls) == 1 and calls[0][0] == "powershell"


def test_a_failed_wmi_fallback_finds_nothing(monkeypatch):
    _processes(monkeypatch, [_Proc(42, "terminal64.exe", None)])

    def broken(*_a, **_kw):
        raise OSError("no powershell")

    monkeypatch.setattr(tp.subprocess, "run", broken)

    assert tp.matching_processes(OURS) == []


# ── the terminal's generation (re-apply after a restart the API did not make) ─


@pytest.fixture
def generations(monkeypatch):
    """A terminal whose process the test controls: `state["running"]` is the
    (pid, create_time) psutil reports, and full lookups are counted."""
    state = {"running": (42, 100.0), "lookups": 0}
    monkeypatch.setattr(tp, "_last_generation", None)

    def find_terminal(_path=None):
        state["lookups"] += 1
        if state["running"] is None:
            raise tp.TerminalPidError("pid_not_found", "not running")
        return state["running"]

    class _Process:
        def __init__(self, pid):
            if state["running"] is None or state["running"][0] != pid:
                raise tp.psutil.NoSuchProcess(pid)
            self._created = state["running"][1]

        def create_time(self):
            return self._created

    monkeypatch.setattr(tp, "find_terminal", find_terminal)
    monkeypatch.setattr(tp.psutil, "Process", _Process)
    return state


def test_the_same_process_needs_no_full_lookup(generations):
    assert tp.current_generation() == (42, 100.0)
    assert tp.current_generation() == (42, 100.0)

    assert generations["lookups"] == 1
    assert tp.last_generation() == (42, 100.0)


def test_a_new_pid_is_a_new_generation(generations):
    tp.current_generation()
    generations["running"] = (77, 200.0)

    assert tp.current_generation() == (77, 200.0)
    assert generations["lookups"] == 2


def test_a_reused_pid_with_another_start_time_is_a_new_generation(generations):
    tp.current_generation()
    generations["running"] = (42, 300.0)

    assert tp.current_generation() == (42, 300.0)
    assert generations["lookups"] == 2


def test_no_running_terminal_is_none_and_the_next_check_looks_again(generations):
    tp.current_generation()
    generations["running"] = None

    assert tp.current_generation() is None
    assert tp.current_generation() is None
    assert generations["lookups"] == 3
