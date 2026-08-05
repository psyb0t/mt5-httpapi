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
    monkeypatch.setattr(jobs, "PRUNE_MIN_ENTRIES", 1)
    _write(tmp_jobs_dir, "olddone", "completed")
    _write(tmp_jobs_dir, "oldfail", "failed")
    _write(tmp_jobs_dir, "newdone", "completed")
    _write(tmp_jobs_dir, "activenow", "running")
    (tmp_jobs_dir / "olddone").mkdir()
    (tmp_jobs_dir / "newdone").mkdir()
    old = time.time() - 10 * 24 * 3600
    for name in ("olddone.json", "oldfail.json"):
        os.utime(tmp_jobs_dir / name, (old, old))
    assert jobs.prune_old_jobs(max_age_seconds=24 * 3600) == 2
    assert not (tmp_jobs_dir / "olddone.json").exists()
    assert not (tmp_jobs_dir / "olddone").exists()
    assert not (tmp_jobs_dir / "oldfail.json").exists()
    assert (tmp_jobs_dir / "newdone.json").exists()
    assert (tmp_jobs_dir / "newdone").exists()
    assert (tmp_jobs_dir / "activenow.json").exists()


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
    assert payload["jobId"] == "xyz"
    assert payload["statusUrl"] == "/backtest/xyz"
    assert payload["reportUrl"] == "/backtest/xyz/report"
    assert payload["logUrl"] == "/backtest/xyz/log"
    assert payload["summary"] == {"netProfit": 1.0}
    assert payload["optimizationType"] == 2
    assert payload["optimizationResults"] == [{"Pass": 7, "Result": 12.5}]
    assert payload["optimizationCache"] == {"name": "cache.opt", "rowCount": 27}
    assert payload["exitCode"] == 0
    assert "queuePosition" not in payload  # completed → no position


def test_store_and_load_job_roundtrip(tmp_jobs_dir):
    job = {"jobId": "rt1", "status": "queued", "submittedAt": "2026-05-12T11:00:00Z"}
    jobs.store_job(job)
    loaded = jobs.load_job("rt1")
    assert loaded["status"] == "queued"
    jobs.update_job("rt1", status="running")
    assert jobs.load_job("rt1")["status"] == "running"


def test_load_job_missing_returns_none(tmp_jobs_dir):
    assert jobs.load_job("nope") is None
