"""Backtest job state: persistent JSON + in-memory cache + summary parser.

Each job has one JSON file at logs/backtest-jobs/<jobId>.json. The in-memory
dict (BACKTEST_JOBS) is a write-through cache guarded by JOB_LOCK. State files
survive API restarts; sweep_orphans() marks any in-flight job as failed at
startup so callers do not poll forever.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone

from mt5api.config import BACKTEST_JOB_DIR
from mt5api.logger import log

JOB_LOCK = threading.Lock()
BACKTEST_JOBS: dict = {}

POLL_AFTER_SECONDS = 60
TERMINAL_STATUSES = frozenset({"completed", "failed"})
ACTIVE_STATUSES = frozenset({"queued", "running"})


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _state_path(job_id: str) -> str:
    os.makedirs(BACKTEST_JOB_DIR, exist_ok=True)
    return os.path.join(BACKTEST_JOB_DIR, f"{job_id}.json")


def _write(job: dict) -> None:
    path = _state_path(job["jobId"])
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(job, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def store_job(job: dict) -> None:
    with JOB_LOCK:
        BACKTEST_JOBS[job["jobId"]] = job
        _write(job)


def update_job(job_id: str, **changes) -> dict:
    with JOB_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if job is None:
            path = _state_path(job_id)
            if not os.path.exists(path):
                raise KeyError(job_id)
            with open(path, "r", encoding="utf-8") as handle:
                job = json.load(handle)
            BACKTEST_JOBS[job_id] = job
        job.update(changes)
        _write(job)
        return dict(job)


def load_job(job_id: str):
    with JOB_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if job is not None:
            return dict(job)
    path = _state_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    with JOB_LOCK:
        BACKTEST_JOBS.setdefault(job_id, job)
    return dict(job)


def queue_position(job_id: str):
    with JOB_LOCK:
        active = sorted(
            (j for j in BACKTEST_JOBS.values() if j.get("status") in ACTIVE_STATUSES),
            key=lambda j: j.get("submittedAt", ""),
        )
    for index, job in enumerate(active):
        if job["jobId"] == job_id:
            return index
    return None


def public_payload(job: dict) -> dict:
    payload = {
        "jobId": job["jobId"],
        "status": job["status"],
        "broker": job.get("broker"),
        "account": job.get("account"),
        "submittedAt": job.get("submittedAt"),
        "startedAt": job.get("startedAt"),
        "finishedAt": job.get("finishedAt"),
        "durationSeconds": job.get("durationSeconds"),
        "reportName": job.get("reportName"),
        "reportUrl": f"/backtest/{job['jobId']}/report",
        "logUrl": f"/backtest/{job['jobId']}/log",
        "statusUrl": f"/backtest/{job['jobId']}",
        "pollAfterSeconds": POLL_AFTER_SECONDS,
        "optimizationType": job.get("optimizationType", 0),
        "optimizationResults": job.get("optimizationResults"),
    }
    pos = queue_position(job["jobId"])
    if pos is not None:
        payload["queuePosition"] = pos
    if job.get("error"):
        payload["error"] = job["error"]
    if job.get("summary") is not None:
        payload["summary"] = job["summary"]
    if job.get("exitCode") is not None:
        payload["exitCode"] = job["exitCode"]
    return payload


def sweep_orphans() -> int:
    """Mark any queued/running jobs on disk as failed.

    Called at API startup. Returns the number of jobs swept.
    """
    if not os.path.isdir(BACKTEST_JOB_DIR):
        return 0
    swept = 0
    for entry in sorted(os.listdir(BACKTEST_JOB_DIR)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(BACKTEST_JOB_DIR, entry)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                job = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("backtest sweep: cannot read %s: %s", entry, exc)
            continue
        if job.get("status") not in ACTIVE_STATUSES:
            continue
        job["status"] = "failed"
        job["error"] = "API restarted before completion"
        job["finishedAt"] = now_iso()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(job, handle, indent=2, sort_keys=True)
        except OSError as exc:
            log.warning("backtest sweep: cannot write %s: %s", entry, exc)
            continue
        swept += 1
        log.info("backtest sweep: marked %s as failed (was %s)", job.get("jobId"), entry)
    return swept


# ── Report summary parser ───────────────────────────────────────────
# MT5 HTML report layout varies by build; treat every field as best-effort
# and return None for anything we cannot parse.

_NUM_RE = re.compile(r"-?\d[\d\s,.]*")

_LABEL_TO_KEY = {
    "Bars": "bars",
    "Ticks": "ticks",
    "Symbols": "symbols",
    "Total Net Profit": "netProfit",
    "Gross Profit": "grossProfit",
    "Gross Loss": "grossLoss",
    "Profit Factor": "profitFactor",
    "Recovery Factor": "recoveryFactor",
    "Expected Payoff": "expectedPayoff",
    "Sharpe Ratio": "sharpeRatio",
    "Balance Drawdown Maximal": "maxDrawdown",
    "Balance Drawdown Absolute": "maxDrawdownAbsolute",
    "Equity Drawdown Maximal": "maxEquityDrawdown",
    "Total Trades": "totalTrades",
    "Total Deals": "totalDeals",
    "Profit Trades": "profitTrades",
    "Loss Trades": "lossTrades",
}


def _to_number(text: str):
    text = text.strip()
    if not text:
        return None
    # Strip currency symbols and percent signs, keep sign and decimal.
    cleaned = text.replace("\u00a0", " ").replace(" ", "").replace(",", "")
    # Drop trailing '%' or trailing '(...)' like "1234.56 (12.34%)".
    cleaned = cleaned.split("(")[0].rstrip("%").strip()
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def parse_report_summary(html: str) -> dict:
    """Best-effort extract a summary block from an MT5 HTML report.

    Returns a dict with all known fields; values are None when not found.
    """
    summary = {key: None for key in _LABEL_TO_KEY.values()}
    if not html:
        return summary
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text)
    for label, key in _LABEL_TO_KEY.items():
        idx = text.find(label)
        if idx < 0:
            continue
        tail = text[idx + len(label): idx + len(label) + 80]
        m = _NUM_RE.search(tail)
        if m:
            summary[key] = _to_number(m.group(0))
    return summary


def is_empty_backtest_summary(summary: dict) -> bool:
    """Return True when MT5 produced a semantically empty report.

    A valid no-trade backtest can still have non-zero Bars/Ticks/Symbols, so only
    treat the report as empty when MT5 reports zero market data across all three.
    """
    return (
        summary.get("bars") == 0
        and summary.get("ticks") == 0
        and summary.get("symbols") == 0
    )
