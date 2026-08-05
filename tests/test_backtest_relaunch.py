"""MT5 self-relaunch (pending LiveUpdate) handling in the backtest runner.

When MT5 has an update queued, the process we launch spawns the updater and
exits 0 within a few seconds, then MT5 relaunches itself and runs the actual
test. `subprocess.run` returns for the first, short-lived process, so the
report does not exist yet — and the job used to be failed as "Report not
generated" while the backtest went on to finish normally minutes later.

These cover both directions: the update restart must be waited out, and a
genuine no-report failure must still fail without inheriting that wait.
"""
from __future__ import annotations

import io
import time
from types import SimpleNamespace

import pytest

from mt5api.backtest import handler, jobs
from mt5api.server import app


def _far_future():
    return time.time() + 300


@pytest.fixture
def terminal(monkeypatch, tmp_path):
    terminal_dir = tmp_path / "terminal"
    terminal_dir.mkdir()
    monkeypatch.setattr(handler, "TERMINAL_DIR", str(terminal_dir))
    monkeypatch.setattr(handler, "RELAUNCH_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(handler, "REPORT_SETTLE_SECONDS", 0)
    monkeypatch.setattr(
        handler, "time", SimpleNamespace(time=time.time, sleep=lambda _s: None)
    )
    return terminal_dir


def _scripted_terminal(monkeypatch, states, *, writes_report_at_exit=None):
    """Drive _terminal_process_alive through a fixed sequence of states.

    The last state repeats once exhausted. If `writes_report_at_exit` is given,
    the file appears the first time the sequence reports the process gone after
    having been alive — MT5 writes the report just before exiting.
    """
    remaining = list(states)
    seen_alive = {"value": False}

    def _alive():
        state = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if state:
            seen_alive["value"] = True
        elif seen_alive["value"] and writes_report_at_exit is not None:
            writes_report_at_exit.write_text("<html>report</html>")
        return state

    monkeypatch.setattr(handler, "_terminal_process_alive", _alive)


def test_waits_out_the_relaunch_and_finds_the_late_report(terminal, monkeypatch):
    """The build-6090 case: the launched process exits with no report, nothing is
    running during the updater gap, a replacement appears, runs the real test,
    and writes the report on its way out."""
    report = terminal / "report.htm"
    _scripted_terminal(
        monkeypatch,
        [False, False, True, True, False],
        writes_report_at_exit=report,
    )

    assert handler._await_self_relaunch("job1", [str(report)], _far_future()) is True
    assert report.exists()


def test_no_relaunch_and_no_report_still_fails(terminal, monkeypatch):
    """A genuine failure must not be rescued: nothing comes back within the grace
    window, so the report stays missing and the caller goes on to fail the job."""
    report = terminal / "report.htm"
    _scripted_terminal(monkeypatch, [False])

    assert handler._await_self_relaunch("job1", [str(report)], _far_future()) is False


def test_report_appearing_during_the_grace_window_is_accepted(terminal, monkeypatch):
    """The report can land in the gap itself, with the process already gone."""
    report = terminal / "report.htm"
    report.write_text("<html>report</html>")
    _scripted_terminal(monkeypatch, [False])

    assert handler._await_self_relaunch("job1", [str(report)], _far_future()) is True


def test_deadline_stops_the_wait_even_while_the_terminal_runs(terminal, monkeypatch):
    """The replacement is still going when the job's timeout budget runs out —
    the wait ends rather than running past the deadline."""
    report = terminal / "report.htm"
    _scripted_terminal(monkeypatch, [True])

    assert handler._await_self_relaunch("job1", [str(report)], time.time() + 0.2) is False


# --- wiring: the short-exit gate in _execute_job ------------------------------

_INI = """[Tester]
Expert=MyEA
Symbol=EURUSD
Period=H1
FromDate=2024.01.01
ToDate=2024.02.01
"""


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    terminal_dir = tmp_path / "terminal"
    terminal_dir.mkdir()
    terminal_path = terminal_dir / "terminal64.exe"
    terminal_path.write_text("stub")
    assets_dir = tmp_path / "assets"
    (assets_dir / "experts").mkdir(parents=True)
    (assets_dir / "sets").mkdir(parents=True)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    monkeypatch.setattr(handler, "TERMINAL_DIR", str(terminal_dir))
    monkeypatch.setattr(handler, "TERMINAL_PATH", str(terminal_path))
    monkeypatch.setattr(handler, "ASSETS_DIR", str(assets_dir))
    monkeypatch.setattr(handler, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(handler, "BACKTEST_JOB_DIR", str(log_dir / "backtest-jobs"))
    monkeypatch.setattr(handler, "BROKER", "testbroker")
    monkeypatch.setattr(handler, "ACCOUNT", "testacct")
    monkeypatch.setattr(jobs, "BACKTEST_JOB_DIR", str(log_dir / "backtest-jobs"))
    jobs.BACKTEST_JOBS.clear()
    monkeypatch.setattr(handler, "_load_account_config", lambda: {
        "login": 1, "password": "p", "server": "S",
    })
    monkeypatch.setattr(handler.threading, "Thread", _NoopThread)
    monkeypatch.setattr("mt5api.server.API_TOKEN", "")
    app.config["TESTING"] = True
    return app.test_client()


def _submit(c):
    resp = c.post(
        "/backtest",
        data={
            "ini": (io.BytesIO(_INI.encode()), "tester.ini"),
            "expert": (io.BytesIO(b"MZstub"), "MyEA.ex5"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    return resp.get_json()["jobId"]


def _run_with_exit_after(monkeypatch, elapsed_seconds):
    """Stub terminal64.exe as a clean exit that took `elapsed_seconds`."""
    real_time = time.time
    offset = {"value": 0.0}

    def _clock():
        return real_time() + offset["value"]

    def _fake_run(*args, **kwargs):
        offset["value"] = elapsed_seconds
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(
        handler, "time", SimpleNamespace(time=_clock, sleep=lambda _s: None)
    )
    monkeypatch.setattr(handler.subprocess, "run", _fake_run)

    waited = []
    monkeypatch.setattr(
        handler, "_await_self_relaunch",
        lambda job_id, reports, deadline: waited.append(job_id) or False,
    )
    return waited


def test_fast_clean_exit_without_report_waits_for_a_relaunch(client, monkeypatch):
    job_id = _submit(client)
    waited = _run_with_exit_after(monkeypatch, 3)

    handler._execute_job(job_id)

    assert waited == [job_id], "a fast clean exit with no report must wait for a relaunch"
    assert jobs.load_job(job_id)["status"] == "failed"


def test_mode3_symbols_xml_counts_as_the_report_and_skips_the_wait(tmp_path):
    """Mode-3 optimizations write `<base>.symbols.xml` rather than the .htm the
    INI asks for. Judging only the .htm would make every finished mode-3 run
    look empty and pay the full relaunch grace window before succeeding."""
    report = tmp_path / "report.htm"
    symbols = tmp_path / "report.symbols.xml"
    symbols.write_text("<xml/>")

    job = {"reportPath": str(report), "optimizationType": 3}
    candidates = handler._report_candidates(job)

    assert str(symbols) in candidates
    assert handler._any_report_exists(candidates) is True

    plain = {"reportPath": str(report), "optimizationType": 0}
    assert handler._any_report_exists(handler._report_candidates(plain)) is False


def test_slow_clean_exit_without_report_fails_without_waiting(client, monkeypatch):
    """A process that ran long enough to have been a real backtest is not an
    update restart, so a missing report there fails immediately."""
    job_id = _submit(client)
    waited = _run_with_exit_after(monkeypatch, handler.RELAUNCH_MAX_EXIT_SECONDS + 5)

    handler._execute_job(job_id)

    assert waited == [], "a long run that produced no report must fail without waiting"
    assert jobs.load_job(job_id)["status"] == "failed"


# --- INI parsing: literals, not interpolation templates -----------------------


def test_percent_in_an_ini_value_is_literal_text():
    """MT5 INI values are literals. A bare '%' is ordinary text in them — EA
    comments and percentage inputs carry it routinely — but configparser's
    default interpolation treats it as a template escape and refuses to parse,
    failing the submission before the test ever runs."""
    parsed = handler._parse_ini(
        "[Tester]\nExpert=MyEA\n[Common]\nComment=Risk 2% per trade\n"
    )

    assert parsed["Common"]["Comment"] == "Risk 2% per trade"


def test_percent_paren_in_an_ini_value_is_not_a_substitution():
    """The shape interpolation would actually try to expand."""
    parsed = handler._parse_ini(
        "[Tester]\nExpert=MyEA\nReport=Reports\\%(name)s.htm\n"
    )

    assert parsed["Tester"]["Report"] == "Reports\\%(name)s.htm"
