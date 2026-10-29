"""POST /backtest/<jobId>/cancel, and a worker that can no longer die silently.

A cancel must stop the run it names and nothing else: a job still waiting for
RUN_LOCK is dropped without touching the run in front of it, a running job has
this terminal's tester processes killed, and the terminal is handed on only
once those processes are gone. A finished job answers as it is.

The worker guard covers the 29 Oct 2026 case: a WinError 5 from the "running"
state write killed the thread before its first `try`, and the job answered
"running" to every poll with no terminal ever started.
"""
from __future__ import annotations

import contextlib
import io
import threading
import time

import pytest

from mt5api.backtest import handler, jobs
from mt5api.server import app

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
    monkeypatch.setattr(handler, "INSTANCE", "default")
    monkeypatch.setattr(jobs, "BACKTEST_JOB_DIR", str(log_dir / "backtest-jobs"))
    monkeypatch.setattr(jobs, "BROKER", "testbroker")
    monkeypatch.setattr(jobs, "ACCOUNT", "testacct")
    monkeypatch.setattr(jobs, "INSTANCE", "default")
    jobs.BACKTEST_JOBS.clear()
    handler._CANCELLED.clear()
    handler._ACTIVE_WORKERS.clear()
    monkeypatch.setattr(handler, "_load_account_config", lambda: {
        "login": 1, "password": "p", "server": "S",
    })
    monkeypatch.setattr(handler.threading, "Thread", _NoopThread)
    monkeypatch.setattr("mt5api.server.API_TOKEN", "")
    # Fast clocks for the waits; nothing here depends on real seconds.
    monkeypatch.setattr(handler, "RUN_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(handler, "CANCEL_POLL_SECONDS", 0.01)
    monkeypatch.setattr(handler, "CANCEL_WAIT_SECONDS", 5)
    monkeypatch.setattr(handler, "_clear_stale_updaters", lambda: 0)
    monkeypatch.setattr(handler.terminal_mcp, "apply", lambda *a, **k: None)
    app.config["TESTING"] = True
    yield app.test_client()
    handler._CANCELLED.clear()
    handler._ACTIVE_WORKERS.clear()


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


# Taken at import, before the fixture swaps handler.threading.Thread for
# _NoopThread: the tests that need a real worker start it themselves.
_ORIGINAL_THREAD = threading.Thread


def _real_thread(target, *args):
    worker = _ORIGINAL_THREAD(target=target, args=args, daemon=True)
    worker.start()
    return worker


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _cancel(c, job_id):
    return c.post(f"/backtest/{job_id}/cancel")


# --- the route ---------------------------------------------------------------


def test_cancel_of_an_unknown_job_is_404_with_a_code(client):
    resp = _cancel(client, "0" * 32)

    assert resp.status_code == 404
    assert resp.get_json()["code"] == "JOB_NOT_FOUND"


def test_cancel_of_a_finished_job_answers_it_unchanged(client, monkeypatch):
    job_id = _submit(client)
    jobs.update_job(job_id, status="completed", finishedAt=jobs.now_iso())
    kills = []
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 0)

    resp = _cancel(client, job_id)

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "completed"
    assert jobs.load_job(job_id)["status"] == "completed"
    assert kills == []


def test_cancel_of_another_terminals_job_is_refused(client, monkeypatch):
    job_id = _submit(client)
    jobs.update_job(job_id, instance="other")

    resp = _cancel(client, job_id)

    assert resp.status_code == 409
    assert resp.get_json()["code"] == "JOB_NOT_OWNED"
    assert jobs.load_job(job_id)["status"] == "queued"


def test_cancel_of_a_job_whose_worker_is_gone_only_marks_it(client, monkeypatch):
    """The 29 Oct shape: the job says running, but no worker runs it any more.
    Nothing is killed, because a later job may be running on this terminal."""
    job_id = _submit(client)
    handler._ACTIVE_WORKERS.discard(job_id)  # the worker thread died
    jobs.update_job(job_id, status="running", startedAt=jobs.now_iso())
    kills = []
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 0)

    resp = _cancel(client, job_id)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "cancelled"
    assert body["stopped"] is True
    assert body["killed"] == 0
    assert kills == []


def test_cancel_of_a_waiting_job_drops_it_without_touching_the_run_in_front(client, monkeypatch):
    job_id = _submit(client)
    kills = []
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 0)
    launches = []
    monkeypatch.setattr(handler.subprocess, "run", lambda *a, **k: launches.append(1))

    handler.RUN_LOCK.acquire()  # another job's run holds the terminal
    try:
        worker = _real_thread(handler._execute_job, job_id)
        assert _wait_for(lambda: job_id in handler._ACTIVE_WORKERS)

        resp = _cancel(client, job_id)
        worker.join(5)
    finally:
        handler.RUN_LOCK.release()

    assert resp.status_code == 200
    assert resp.get_json()["stopped"] is True
    assert kills == [], "the run in front owns this terminal's processes"
    assert launches == []
    assert jobs.load_job(job_id)["status"] == "cancelled"


def test_cancel_of_a_running_job_kills_its_terminal_and_waits_for_the_worker(client, monkeypatch):
    job_id = _submit(client)
    killed = threading.Event()

    def _fake_run(*args, **kwargs):
        # terminal64.exe runs until something kills it.
        assert killed.wait(5), "the cancel never killed the terminal"
        return type("Result", (), {"returncode": 1})()

    def _fake_kill(**_kwargs):
        killed.set()
        return 1

    monkeypatch.setattr(handler.subprocess, "run", _fake_run)
    monkeypatch.setattr(handler, "kill_terminal_processes", _fake_kill)
    monkeypatch.setattr(handler, "_terminal_processes", lambda *a, **k: iter(()))

    worker = _real_thread(handler._execute_job, job_id)
    assert _wait_for(lambda: handler._LOCK_HOLDER["job_id"] == job_id)
    assert _wait_for(lambda: jobs.load_job(job_id)["status"] == "running")

    resp = _cancel(client, job_id)
    worker.join(5)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "cancelled"
    assert body["stopped"] is True
    assert body["killed"] >= 1
    job = jobs.load_job(job_id)
    # The killed terminal exits 1; the worker's late "failed" must not win.
    assert job["status"] == "cancelled"
    assert job["error"] == handler.CANCEL_ERROR
    assert handler._LOCK_HOLDER["job_id"] is None
    assert not handler.RUN_LOCK.locked()


def test_cancel_answers_202_while_the_worker_is_still_stopping(client, monkeypatch):
    job_id = _submit(client)  # its worker never runs (_NoopThread), so it never stops
    monkeypatch.setattr(handler, "CANCEL_WAIT_SECONDS", 0.05)

    resp = _cancel(client, job_id)

    assert resp.status_code == 202
    assert resp.get_json()["stopped"] is False
    assert jobs.load_job(job_id)["status"] == "cancelled"


def test_cancelled_run_waits_for_its_agents_before_letting_go(client, monkeypatch):
    """A metatester64 agent can outlive terminal64.exe; launching the next job
    while it still holds the agent ports fails with bind error 10048."""
    alive = [["agent"], ["agent"], []]
    checks = []

    def _processes(*_a, **_k):
        checks.append(1)
        return iter(alive.pop(0) if len(alive) > 1 else alive[0])

    monkeypatch.setattr(handler, "_terminal_processes", _processes)
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: 1)
    monkeypatch.setattr(handler.time, "sleep", lambda _s: None)

    handler._settle_after_cancel("job1")

    assert len(checks) == 3


def test_cancelled_run_kills_again_when_agents_outstay_the_settle_window(client, monkeypatch):
    kills = []
    monkeypatch.setattr(handler, "_terminal_processes", lambda *a, **k: iter(["agent"]))
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 1)
    monkeypatch.setattr(handler, "CANCEL_SETTLE_SECONDS", 0)

    handler._settle_after_cancel("job1")

    assert len(kills) == 2


def test_cancel_during_the_self_relaunch_wait_stops_waiting(client, monkeypatch):
    """MT5's pending-LiveUpdate hand-off leaves a gap with no terminal at all;
    a cancel there must end the wait instead of sitting out the grace window."""
    handler._CANCELLED.add("job1")
    monkeypatch.setattr(handler, "_terminal_process_alive", lambda: False)
    monkeypatch.setattr(handler, "_any_report_exists", lambda _paths: False)
    started = time.time()

    assert handler._await_self_relaunch("job1", [], time.time() + 60) is False
    assert time.time() - started < 5


# --- the worker guard ----------------------------------------------------------


def test_an_exception_outside_the_runs_try_fails_the_job(client, monkeypatch):
    """The 29 Oct case: the "running" write raised, before the body's `try`."""
    job_id = _submit(client)
    real_update = jobs.update_job

    def _update(jid, **changes):
        if changes.get("status") == "running":
            raise PermissionError("[WinError 5] Access is denied")
        return real_update(jid, **changes)

    monkeypatch.setattr(handler.jobs, "update_job", _update)

    handler._execute_job(job_id)

    job = jobs.load_job(job_id)
    assert job["status"] == "failed"
    assert job["error"].startswith(handler.WORKER_CRASH_ERROR)
    assert "WinError 5" in job["error"]
    assert job_id not in handler._ACTIVE_WORKERS
    assert not handler.RUN_LOCK.locked()


def test_the_guard_leaves_a_finished_job_alone(client, monkeypatch):
    job_id = _submit(client)
    jobs.update_job(job_id, status="completed")
    monkeypatch.setattr(handler, "_run_job", lambda _jid: (_ for _ in ()).throw(RuntimeError("x")))

    handler._execute_job(job_id)

    assert jobs.load_job(job_id)["status"] == "completed"


def test_a_worker_that_cannot_start_fails_the_job_with_503(client, monkeypatch):
    class _BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(handler.threading, "Thread", _BrokenThread)

    resp = client.post(
        "/backtest",
        data={
            "ini": (io.BytesIO(_INI.encode()), "tester.ini"),
            "expert": (io.BytesIO(b"MZstub"), "MyEA.ex5"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 503
    assert resp.get_json()["code"] == "WORKER_START_FAILED"
    (job,) = jobs.BACKTEST_JOBS.values()
    assert job["status"] == "failed"
    assert job["error"] == handler.WORKER_START_ERROR
    assert handler._ACTIVE_WORKERS == set()


# --- a cancel after the worker has left RUN_LOCK ------------------------------


def test_a_cancel_after_the_lock_retakes_it_and_kills_mt5s_replacement(client, monkeypatch):
    """After the launched process exits, MT5 can start the real run seconds later
    (a LiveUpdate hand-off); that terminal must be killed too."""
    seen = [[], ["replacement"], []]
    kills = []
    monkeypatch.setattr(
        handler, "_terminal_processes",
        lambda *a, **k: iter(seen.pop(0) if len(seen) > 1 else seen[0]),
    )
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 1)
    monkeypatch.setattr(handler, "RELAUNCH_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(handler.time, "sleep", lambda _s: None)

    handler._stop_after_lock_released("job1")

    assert len(kills) == 2  # the first sweep, and the replacement when it appeared
    assert not handler.RUN_LOCK.locked()
    assert handler._LOCK_HOLDER["job_id"] is None


def test_a_cancel_after_the_lock_leaves_the_next_jobs_run_alone(client, monkeypatch):
    kills = []
    monkeypatch.setattr(handler, "kill_terminal_processes", lambda **_k: kills.append(1) or 1)
    monkeypatch.setattr(handler, "CANCEL_SETTLE_SECONDS", 0.05)

    handler.RUN_LOCK.acquire()  # the next job is running on this terminal
    try:
        handler._stop_after_lock_released("job1")
    finally:
        handler.RUN_LOCK.release()

    assert kills == []


def test_cancel_of_a_job_that_finishes_meanwhile_answers_it_finished(client, monkeypatch):
    job_id = _submit(client)
    real_cancel = jobs.cancel_if_active

    def _finishes_first(jid, **changes):
        jobs.update_job(jid, status="completed")
        return real_cancel(jid, **changes)

    monkeypatch.setattr(handler.jobs, "cancel_if_active", _finishes_first)

    resp = _cancel(client, job_id)

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "completed"
    assert jobs.load_job(job_id)["status"] == "completed"
    assert job_id not in handler._CANCELLED


# --- a cancel the worker notices only after RUN_LOCK ---------------------------


def _land_cancel(job_id):
    """What POST /cancel does to the job and its flag, at a chosen moment."""
    with handler._CANCEL_LOCK:
        handler._CANCELLED.add(job_id)
    jobs.cancel_if_active(job_id, error=handler.CANCEL_ERROR, finishedAt=jobs.now_iso())


def test_a_cancel_after_the_terminal_exits_stops_the_run_instead_of_judging_it(
    client, monkeypatch
):
    """The cancel lands between the terminal's exit and the report checks: the
    worker takes the terminal back and stops whatever is left, and no late
    "failed" reaches the job."""
    job_id = _submit(client)
    monkeypatch.setattr(
        handler.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 1})()
    )
    real_lock = handler._run_lock

    @contextlib.contextmanager
    def _lock_then_cancel(jid):
        with real_lock(jid):
            yield
        _land_cancel(jid)

    monkeypatch.setattr(handler, "_run_lock", _lock_then_cancel)
    stopped = []
    monkeypatch.setattr(handler, "_stop_after_lock_released", stopped.append)

    handler._execute_job(job_id)

    assert stopped == [job_id]
    job = jobs.load_job(job_id)
    assert job["status"] == "cancelled"
    assert job["error"] == handler.CANCEL_ERROR
    assert job.get("exitCode") is None
    assert job_id not in handler._ACTIVE_WORKERS


def test_a_cancel_during_the_self_relaunch_wait_stops_mt5s_replacement(client, monkeypatch):
    """A clean, fast exit with no report sends the worker to wait for MT5's
    replacement terminal. A cancel there must stop that terminal, not wait for
    it or fail the job for a missing report."""
    job_id = _submit(client)
    monkeypatch.setattr(
        handler.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 0})()
    )
    monkeypatch.setattr(handler, "_any_report_exists", lambda _paths: False)

    def _cancelled_while_waiting(jid, _paths, _deadline):
        _land_cancel(jid)
        return False

    monkeypatch.setattr(handler, "_await_self_relaunch", _cancelled_while_waiting)
    stopped = []
    monkeypatch.setattr(handler, "_stop_after_lock_released", stopped.append)

    handler._execute_job(job_id)

    assert stopped == [job_id]
    job = jobs.load_job(job_id)
    assert job["status"] == "cancelled"
    assert job["error"] == handler.CANCEL_ERROR
    assert not handler.RUN_LOCK.locked()
