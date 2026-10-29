"""Unit tests for mt5api.backtest.jobs sweep + retention + summary parser."""
from __future__ import annotations

import json
import os
import time

import pytest

from mt5api.backtest import jobs


@pytest.fixture
def tmp_jobs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "BACKTEST_JOB_DIR", str(tmp_path))
    # Default to a single-terminal install so legacy (un-instanced) jobs are
    # claimed. Ownership-scoping tests override this via _clone_count.
    monkeypatch.setattr(jobs, "_configured_terminals", lambda b, a: 1)
    # Reset in-memory cache between tests.
    jobs.BACKTEST_JOBS.clear()
    return tmp_path


def _write(tmp_path, job_id, status, **extra):
    payload = {"jobId": job_id, "status": status, "submittedAt": "2026-05-12T10:00:00Z"}
    payload.update(extra)
    (tmp_path / f"{job_id}.json").write_text(json.dumps(payload))


def test_sweep_marks_running_and_queued_as_failed(tmp_jobs_dir):
    _write(tmp_jobs_dir, "abc", "running")
    _write(tmp_jobs_dir, "def", "queued")
    swept = jobs.sweep_orphans()
    assert swept == 2
    for jid in ("abc", "def"):
        data = json.loads((tmp_jobs_dir / f"{jid}.json").read_text())
        assert data["status"] == "failed"
        assert data["error"] == "API restarted before completion"
        assert data["finishedAt"]


def test_sweep_leaves_terminal_jobs_alone(tmp_jobs_dir):
    _write(tmp_jobs_dir, "ok", "completed", summary={"netProfit": 12.5})
    _write(tmp_jobs_dir, "bad", "failed", error="boom")
    swept = jobs.sweep_orphans()
    assert swept == 0
    assert json.loads((tmp_jobs_dir / "ok.json").read_text())["status"] == "completed"
    assert json.loads((tmp_jobs_dir / "bad.json").read_text())["error"] == "boom"


def test_sweep_handles_corrupt_files(tmp_jobs_dir):
    (tmp_jobs_dir / "broken.json").write_text("{not json")
    _write(tmp_jobs_dir, "live", "running")
    assert jobs.sweep_orphans() == 1


def test_sweep_skips_stale_files(tmp_jobs_dir):
    _write(tmp_jobs_dir, "fresh", "running")
    _write(tmp_jobs_dir, "stale", "running")
    old = time.time() - jobs.SWEEP_LOOKBACK_SECONDS - 3600
    os.utime(tmp_jobs_dir / "stale.json", (old, old))
    assert jobs.sweep_orphans() == 1
    assert json.loads((tmp_jobs_dir / "fresh.json").read_text())["status"] == "failed"
    assert json.loads((tmp_jobs_dir / "stale.json").read_text())["status"] == "running"


def test_sweep_honors_custom_lookback(tmp_jobs_dir):
    _write(tmp_jobs_dir, "oldish", "running")
    old = time.time() - 7200
    os.utime(tmp_jobs_dir / "oldish.json", (old, old))
    assert jobs.sweep_orphans(lookback_seconds=3600) == 0
    assert jobs.sweep_orphans(lookback_seconds=10800) == 1


def test_sweep_no_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "BACKTEST_JOB_DIR", str(tmp_path / "missing"))
    assert jobs.sweep_orphans() == 0


def test_prune_removes_old_terminal_jobs_and_staging(tmp_jobs_dir, monkeypatch):
    # jobId values MUST be real 32-hex uuid4().hex shapes here: prune's staging
    # removal now refuses anything else (see _JOB_ID_RE), so this also proves
    # the positive/legitimate-jobId path still works under that guard.
    olddone_id = "a1" * 16
    oldfail_id = "b2" * 16
    newdone_id = "c3" * 16
    activenow_id = "d4" * 16
    monkeypatch.setattr(jobs, "PRUNE_MIN_ENTRIES", 1)
    _write(tmp_jobs_dir, olddone_id, "completed")
    _write(tmp_jobs_dir, oldfail_id, "failed")
    _write(tmp_jobs_dir, newdone_id, "completed")
    _write(tmp_jobs_dir, activenow_id, "running")
    (tmp_jobs_dir / olddone_id).mkdir()
    (tmp_jobs_dir / newdone_id).mkdir()
    old = time.time() - 10 * 24 * 3600
    for jid in (olddone_id, oldfail_id):
        os.utime(tmp_jobs_dir / f"{jid}.json", (old, old))
    assert jobs.prune_old_jobs(max_age_seconds=24 * 3600) == 2
    assert not (tmp_jobs_dir / f"{olddone_id}.json").exists()
    assert not (tmp_jobs_dir / olddone_id).exists()
    assert not (tmp_jobs_dir / f"{oldfail_id}.json").exists()
    assert (tmp_jobs_dir / f"{newdone_id}.json").exists()
    assert (tmp_jobs_dir / newdone_id).exists()
    assert (tmp_jobs_dir / f"{activenow_id}.json").exists()


def test_prune_refuses_traversal_jobid_and_only_removes_json(tmp_jobs_dir, monkeypatch, caplog):
    """jobId is read back from a state file inside the shared job dir, so
    prune must not trust it to be a bare 32-hex id. A traversal-shaped jobId
    (non-32-hex) must not turn into an rmtree target — only the .json is
    removed, and the refusal is logged.
    """
    monkeypatch.setattr(jobs, "PRUNE_MIN_ENTRIES", 1)
    decoy = tmp_jobs_dir / "escaped-decoy"
    decoy.mkdir()
    # Resolves back inside tmp_jobs_dir (parent + tmp dir's own name), so the
    # decoy proves what an unguarded rmtree would have hit without leaving
    # the test's sandbox.
    traversal_job_id = f"../{tmp_jobs_dir.name}/escaped-decoy"
    _write(tmp_jobs_dir, "traversal", "completed", jobId=traversal_job_id)
    old = time.time() - 10 * 24 * 3600
    os.utime(tmp_jobs_dir / "traversal.json", (old, old))
    with caplog.at_level("WARNING", logger="mt5api"):
        pruned = jobs.prune_old_jobs(max_age_seconds=24 * 3600)
    assert pruned == 1
    assert not (tmp_jobs_dir / "traversal.json").exists()
    assert decoy.exists()
    assert any(
        "suspicious jobId" in rec.message and traversal_job_id in rec.message
        for rec in caplog.records
    )


def test_remove_job_state_none_job_id_removes_json_only(tmp_jobs_dir):
    json_path = tmp_jobs_dir / "solo.json"
    json_path.write_text("{}")
    decoy = tmp_jobs_dir / "solo"
    decoy.mkdir()
    jobs._remove_job_state(str(json_path), None)
    assert not json_path.exists()
    assert decoy.exists()


def test_prune_keeps_active_jobs_even_when_old(tmp_jobs_dir, monkeypatch):
    monkeypatch.setattr(jobs, "PRUNE_MIN_ENTRIES", 1)
    _write(tmp_jobs_dir, "stalequeued", "queued")
    old = time.time() - 10 * 24 * 3600
    os.utime(tmp_jobs_dir / "stalequeued.json", (old, old))
    assert jobs.prune_old_jobs(max_age_seconds=24 * 3600) == 0
    assert (tmp_jobs_dir / "stalequeued.json").exists()


def test_prune_skips_small_dir(tmp_jobs_dir):
    _write(tmp_jobs_dir, "olddone", "completed")
    old = time.time() - 10 * 24 * 3600
    os.utime(tmp_jobs_dir / "olddone.json", (old, old))
    assert jobs.prune_old_jobs() == 0
    assert (tmp_jobs_dir / "olddone.json").exists()


def test_prune_honors_marker_interval(tmp_jobs_dir, monkeypatch):
    monkeypatch.setattr(jobs, "PRUNE_MIN_ENTRIES", 1)
    _write(tmp_jobs_dir, "olddone", "completed")
    old = time.time() - 10 * 24 * 3600
    os.utime(tmp_jobs_dir / "olddone.json", (old, old))
    (tmp_jobs_dir / jobs.PRUNE_MARKER_NAME).write_text(str(int(time.time())))
    assert jobs.prune_old_jobs() == 0
    assert (tmp_jobs_dir / "olddone.json").exists()


def test_summary_parser_returns_all_keys_for_empty_html():
    summary = jobs.parse_report_summary("")
    expected_keys = {
        "netProfit", "grossProfit", "grossLoss", "profitFactor",
        "recoveryFactor", "expectedPayoff", "sharpeRatio",
        "maxDrawdown", "maxDrawdownAbsolute", "maxEquityDrawdown",
        "totalTrades", "totalDeals", "profitTrades", "lossTrades",
        "bars", "ticks", "symbols",
    }
    assert set(summary.keys()) == expected_keys
    assert all(v is None for v in summary.values())


def test_summary_parser_extracts_numbers():
    html = """
    <html><body><table>
      <tr><td>Total Net Profit</td><td>1 234.56</td></tr>
      <tr><td>Profit Factor</td><td>1.42</td></tr>
      <tr><td>Total Trades</td><td>87</td></tr>
      <tr><td>Balance Drawdown Maximal</td><td>234.50 (5.12%)</td></tr>
    </table></body></html>
    """
    summary = jobs.parse_report_summary(html)
    assert summary["netProfit"] == 1234.56
    assert summary["profitFactor"] == 1.42
    assert summary["totalTrades"] == 87
    assert summary["maxDrawdown"] == 234.50


def test_public_payload_shape(tmp_jobs_dir):
    job = {
        "jobId": "xyz",
        "status": "completed",
        "broker": "darwinex",
        "account": "live",
        "submittedAt": "2026-05-12T10:00:00Z",
        "startedAt": "2026-05-12T10:00:01Z",
        "finishedAt": "2026-05-12T10:30:00Z",
        "durationSeconds": 1799.0,
        "reportName": "r.htm",
        "summary": {"netProfit": 1.0},
        "optimizationType": 2,
        "optimizationResults": [{"Pass": 7, "Result": 12.5}],
        "optimizationCache": {"name": "cache.opt", "rowCount": 27},
        "exitCode": 0,
    }
    payload = jobs.public_payload(job)
    assert payload["job_id"] == "xyz"
    assert payload["status_url"] == "/backtest/xyz"
    assert payload["report_url"] == "/backtest/xyz/report"
    assert payload["log_url"] == "/backtest/xyz/log"
    assert payload["summary"] == {"net_profit": 1.0, "netProfit": 1.0}
    assert payload["optimization_type"] == 2
    # Rows are data keyed by the report's columns and the EA's inputs.
    assert payload["optimization_results"] == [{"Pass": 7, "Result": 12.5}]
    assert payload["optimization_cache"] == {
        "name": "cache.opt",
        "row_count": 27,
        "rowCount": 27,
    }
    assert payload["exit_code"] == 0
    assert "queue_position" not in payload  # completed → no position


def test_public_payload_keeps_every_legacy_camel_case_key(tmp_jobs_dir):
    """The camelCase names are deprecated but still sent, each with the
    same value as its snake_case twin."""
    job = {
        "jobId": "legacy",
        "status": "running",
        "submittedAt": "2026-05-12T10:00:00Z",
        "durationSeconds": 3.0,
        "optimizationType": 0,
    }
    payload = jobs.public_payload(job)

    pairs = {
        "jobId": "job_id",
        "submittedAt": "submitted_at",
        "durationSeconds": "duration_seconds",
        "statusUrl": "status_url",
        "pollAfterSeconds": "poll_after_seconds",
        "optimizationType": "optimization_type",
    }
    for camel, snake in pairs.items():
        assert payload[camel] == payload[snake], camel


def test_store_and_load_job_roundtrip(tmp_jobs_dir):
    job = {"jobId": "rt1", "status": "queued", "submittedAt": "2026-05-12T11:00:00Z"}
    jobs.store_job(job)
    loaded = jobs.load_job("rt1")
    assert loaded["status"] == "queued"
    jobs.update_job("rt1", status="running")
    assert jobs.load_job("rt1")["status"] == "running"


def test_load_job_missing_returns_none(tmp_jobs_dir):
    assert jobs.load_job("nope") is None


# ── Ownership scoping ────────────────────────────────────────────────────────
#
# Every backtest API on a host shares one job directory, and sibling terminals
# share broker+account (they differ only by instance). Before scoping, any
# terminal's restart failed every other terminal's in-flight run.


def _identity(monkeypatch, broker="darwinex", account="live", instance="a"):
    monkeypatch.setattr(jobs, "BROKER", broker)
    monkeypatch.setattr(jobs, "ACCOUNT", account)
    monkeypatch.setattr(jobs, "INSTANCE", instance)


def _clone_count(monkeypatch, count):
    monkeypatch.setattr(jobs, "_configured_terminals", lambda b, a: count)


def test_sweep_leaves_a_sibling_terminals_job_alone(tmp_jobs_dir, monkeypatch):
    _identity(monkeypatch, instance="a")
    _clone_count(monkeypatch, 3)
    _write(tmp_jobs_dir, "mine", "running", broker="darwinex", account="live", instance="a")
    _write(tmp_jobs_dir, "theirs", "running", broker="darwinex", account="live", instance="b")

    assert jobs.sweep_orphans() == 1

    mine = json.loads((tmp_jobs_dir / "mine.json").read_text())
    theirs = json.loads((tmp_jobs_dir / "theirs.json").read_text())
    assert mine["status"] == "failed"
    assert theirs["status"] == "running", "a sibling terminal's live run must survive our restart"


def test_sweep_does_not_claim_a_legacy_job_on_a_multi_clone_install(tmp_jobs_dir, monkeypatch):
    # The real pre-PR shape: broker+account present, instance absent. On a
    # multi-clone install every clone shares broker+account, so the job cannot
    # be attributed — claiming it would fail a sibling's live run. It must be
    # left alone (retention retires it eventually).
    _identity(monkeypatch, instance="a")
    _clone_count(monkeypatch, 3)
    _write(tmp_jobs_dir, "legacy", "running", broker="darwinex", account="live")

    assert jobs.sweep_orphans() == 0
    assert json.loads((tmp_jobs_dir / "legacy.json").read_text())["status"] == "running"


def test_sweep_still_claims_a_legacy_job_on_a_single_terminal_install(tmp_jobs_dir, monkeypatch):
    # What a single-terminal install writes: broker+account, no instance, and no
    # sibling clone to be confused with. Behaviour must not change there.
    _identity(monkeypatch)
    _clone_count(monkeypatch, 1)
    _write(tmp_jobs_dir, "legacy", "running", broker="darwinex", account="live")

    assert jobs.sweep_orphans() == 1
    assert json.loads((tmp_jobs_dir / "legacy.json").read_text())["status"] == "failed"


def test_sweep_skips_another_broker_or_account(tmp_jobs_dir, monkeypatch):
    _identity(monkeypatch, broker="darwinex", account="live", instance="a")
    _write(tmp_jobs_dir, "other-broker", "running", broker="icmarkets", account="live", instance="a")
    _write(tmp_jobs_dir, "other-account", "running", broker="darwinex", account="demo", instance="a")
    assert jobs.sweep_orphans() == 0


def test_owns_job_normalizes_the_instance(monkeypatch):
    # "" and None mean the default instance; they must not read as a stranger.
    _identity(monkeypatch, instance="default")
    _clone_count(monkeypatch, 1)
    assert jobs.owns_job({"broker": "darwinex", "account": "live", "instance": ""})
    assert jobs.owns_job({"broker": "darwinex", "account": "live", "instance": None})
    assert jobs.owns_job({"broker": "darwinex", "account": "live", "instance": "default"})
    assert not jobs.owns_job({"broker": "darwinex", "account": "live", "instance": "a"})


# --- state writes on the VM share, and the cancelled status -------------------


def test_rename_is_retried_through_a_brief_sharing_violation(tmp_jobs_dir, monkeypatch):
    """os.replace on the share fails with WinError 5 while another handle has
    the target open; it clears in milliseconds."""
    real_replace = os.replace
    failures = {"left": 2}

    def _replace(src, dst):
        if failures["left"]:
            failures["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(jobs.os, "replace", _replace)
    monkeypatch.setattr(jobs.time, "sleep", lambda _s: None)

    jobs.store_job({"jobId": "a" * 32, "status": "queued"})

    assert json.loads((tmp_jobs_dir / f"{'a' * 32}.json").read_text())["status"] == "queued"


def test_a_write_that_keeps_failing_does_not_take_the_run_down(tmp_jobs_dir, monkeypatch):
    """The 29 Oct 2026 crash: the job carries on from memory, and the next
    update writes the whole job again."""
    job_id = "b" * 32
    jobs.store_job({"jobId": job_id, "status": "queued"})
    real_replace = os.replace
    broken = {"on": True}

    def _replace(src, dst):
        if broken["on"]:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(jobs.os, "replace", _replace)
    monkeypatch.setattr(jobs.time, "sleep", lambda _s: None)

    updated = jobs.update_job(job_id, status="running")

    assert updated["status"] == "running"
    assert jobs.load_job(job_id)["status"] == "running"
    assert json.loads((tmp_jobs_dir / f"{job_id}.json").read_text())["status"] == "queued"

    broken["on"] = False
    jobs.update_job(job_id, launchAttempts=1)
    on_disk = json.loads((tmp_jobs_dir / f"{job_id}.json").read_text())
    assert on_disk["status"] == "running"
    assert on_disk["launchAttempts"] == 1


def test_a_cancelled_job_keeps_its_status_against_a_late_write(tmp_jobs_dir):
    job_id = "c" * 32
    jobs.store_job({"jobId": job_id, "status": "running"})
    jobs.update_job(job_id, status="cancelled", error="Cancelled by request", finishedAt="t1")

    late = jobs.update_job(
        job_id, status="failed", error="terminal64.exe exited with code 1",
        finishedAt="t2", exitCode=1, durationSeconds=12.0,
    )

    assert late["status"] == "cancelled"
    assert late["error"] == "Cancelled by request"
    assert late["finishedAt"] == "t1"
    assert late["exitCode"] == 1
    assert late["durationSeconds"] == 12.0


def test_cancelled_is_final_for_the_sweep_and_the_prune(tmp_jobs_dir):
    _write(tmp_jobs_dir, "gone", "cancelled")
    assert jobs.sweep_orphans() == 0
    assert "cancelled" in jobs.TERMINAL_STATUSES
    assert "cancelled" not in jobs.ACTIVE_STATUSES


def test_an_unsaved_job_is_written_again_on_the_next_poll(tmp_jobs_dir, monkeypatch):
    """A lost final write must not leave the disk saying "running": the rolling
    restart reads that as busy, and a restart's sweep would fail a finished run."""
    job_id = "d" * 32
    jobs.store_job({"jobId": job_id, "status": "running"})
    real_replace = os.replace
    broken = {"on": True}

    def _replace(src, dst):
        if broken["on"]:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(jobs.os, "replace", _replace)
    monkeypatch.setattr(jobs.time, "sleep", lambda _s: None)
    jobs.update_job(job_id, status="completed")
    assert job_id in jobs.UNSAVED_JOBS

    broken["on"] = False
    jobs.load_job(job_id)

    assert json.loads((tmp_jobs_dir / f"{job_id}.json").read_text())["status"] == "completed"
    assert job_id not in jobs.UNSAVED_JOBS


def test_cancel_if_active_leaves_a_finished_job_alone(tmp_jobs_dir):
    jobs.store_job({"jobId": "e" * 32, "status": "completed"})
    assert jobs.cancel_if_active("e" * 32, error="x") is None
    assert jobs.load_job("e" * 32)["status"] == "completed"
    jobs.store_job({"jobId": "f" * 32, "status": "running"})
    assert jobs.cancel_if_active("f" * 32, error="x")["status"] == "cancelled"
