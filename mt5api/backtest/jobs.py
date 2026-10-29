"""Backtest job state: persistent JSON + in-memory cache + summary parser.

Each job has one JSON file at logs/backtest-jobs/<jobId>.json. The in-memory
dict (BACKTEST_JOBS) is a write-through cache guarded by JOB_LOCK. State files
survive API restarts; sweep_orphans() marks any in-flight job as failed at
startup so callers do not poll forever, and prune_old_jobs() retires terminal
(completed/failed) jobs older than the retention window so the directory cannot
grow without bound. Every backtest API on a VM shares this directory, so both
passes only inspect recently-touched files and are safe to run concurrently.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone

from mt5api.config import (
    ACCOUNT,
    BACKTEST_JOB_DIR,
    BACKTEST_JOB_RETENTION_SECONDS,
    BACKTEST_SWEEP_LOOKBACK_SECONDS,
    BROKER,
    INSTANCE,
    load_yaml_config,
    normalize_instance,
)
from mt5api.jsonkeys import with_legacy_keys
from mt5api.logger import log

JOB_LOCK = threading.Lock()
BACKTEST_JOBS: dict = {}
# Jobs whose latest state is in memory but not on disk (a write failed). The
# next load_job or update_job writes them again; see _write.
UNSAVED_JOBS: set = set()

POLL_AFTER_SECONDS = 60
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
# Once a job is cancelled these fields keep the cancel's values: the worker can
# still be winding down when POST /backtest/<id>/cancel answers, and its late
# "failed" or "completed" must not turn a cancelled job back into a finished one.
CANCEL_KEEPS_FIELDS = frozenset({"status", "error", "finishedAt"})

# os.replace on the VM share fails with WinError 5 while any other handle has
# the target open (a reader, Defender, an SMB oplock break). It clears within
# milliseconds, so retry before giving up. On a two-VM install one such
# failure killed a job's worker before its terminal started.
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)

# Only state files touched within the last SWEEP_LOOKBACK_SECONDS can be an
# active run that needs sweeping; anything older was long since terminal.
SWEEP_LOOKBACK_SECONDS = BACKTEST_SWEEP_LOOKBACK_SECONDS
# prune_old_jobs() retires terminal jobs older than JOB_RETENTION_SECONDS.
JOB_RETENTION_SECONDS = BACKTEST_JOB_RETENTION_SECONDS
# Don't bother listing a directory unless it has grown this big.
PRUNE_MIN_ENTRIES = 1000
# All APIs on a VM share the job dir, so gate pruning with a marker file: only
# the first process to pass the gate scans the whole directory per interval.
PRUNE_INTERVAL_SECONDS = 24 * 3600
PRUNE_MARKER_NAME = ".job-prune-marker"

# _remove_job_state does an unbounded shutil.rmtree keyed on jobId read back
# from a state file inside BACKTEST_JOB_DIR — a directory every backtest API
# process on the VM writes to. That delete must not trust the writer to be
# well-behaved: a corrupted/malicious jobId (e.g. "../../etc") must never turn
# into a path-traversal rmtree target. Every legitimate jobId is
# uuid.uuid4().hex — 32 lowercase hex chars (see handler.py).
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _state_path(job_id: str) -> str:
    os.makedirs(BACKTEST_JOB_DIR, exist_ok=True)
    return os.path.join(BACKTEST_JOB_DIR, f"{job_id}.json")


def _atomic_write(path: str, job: dict) -> None:
    """Write job state atomically (tmp + rename).

    A concurrent reader — including a sweep running in another API process that
    shares this directory — must never observe a truncated file.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(job, handle, indent=2, sort_keys=True)
    for delay in REPLACE_RETRY_DELAYS:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(tmp, path)


def _write(job: dict) -> None:
    """Persist a job, or log why not.

    The in-memory copy is what every poll is answered from while this process
    lives, so a disk write that still fails after the retries must not take the
    run down with it: the next update writes the whole job again, and after a
    restart sweep_orphans() fails whatever the disk still calls active. A job
    left unsaved is written again by the next load_job (every poll) or
    update_job, so a lost final write does not leave the disk saying "running"
    (which a restart's sweep_orphans() would then fail).
    """
    try:
        _atomic_write(_state_path(job["jobId"]), job)
    except OSError as exc:
        UNSAVED_JOBS.add(job["jobId"])
        log.warning(
            "backtest job %s: state not saved to disk, kept in memory: %s", job["jobId"], exc
        )
        return
    UNSAVED_JOBS.discard(job["jobId"])


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
        if job.get("status") == "cancelled":
            changes = {k: v for k, v in changes.items() if k not in CANCEL_KEEPS_FIELDS}
        job.update(changes)
        _write(job)
        return dict(job)


def load_job(job_id: str):
    with JOB_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if job is not None:
            if job_id in UNSAVED_JOBS:
                _write(job)
            return dict(job)
    path = _state_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    with JOB_LOCK:
        BACKTEST_JOBS.setdefault(job_id, job)
    return dict(job)


def cancel_if_active(job_id: str, **changes):
    """Mark a queued or running job cancelled, atomically with the check.

    Returns the updated job, or None when it had already finished (or is
    unknown): a job completing between a caller's check and its write must not
    turn into a cancelled one.
    """
    with JOB_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if job is None or job.get("status") not in ACTIVE_STATUSES:
            return None
        job.update(changes, status="cancelled")
        _write(job)
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
        "optimizationCache": job.get("optimizationCache"),
    }
    pos = queue_position(job["jobId"])
    if pos is not None:
        payload["queuePosition"] = pos
    if job.get("error"):
        payload["error"] = job["error"]
    if job.get("summary") is not None:
        payload["summary"] = with_legacy_keys(job["summary"])
    if job.get("exitCode") is not None:
        payload["exitCode"] = job["exitCode"]
    if payload["optimizationCache"]:
        payload["optimizationCache"] = with_legacy_keys(payload["optimizationCache"])
    # optimizationResults rows are keyed by the report's own columns and the
    # EA's input names: data, left as they are.
    return with_legacy_keys(payload)


def _configured_terminals(broker: str, account: str) -> int:
    """How many terminals in config.yaml target this broker/account.

    Reads the real terminal list so the ownership decision reflects how the
    install is actually configured, not just this API process's own identity.
    """
    terms = load_yaml_config().get("terminals") or []
    return sum(
        1
        for t in terms
        if t.get("broker") == broker and t.get("account", "") == account
    )


def owns_job(job: dict) -> bool:
    """Is this job ours, rather than a sibling terminal's?

    Every backtest API on a host shares one job directory, so the sweep sees
    every terminal's jobs. A job that names a terminal is ours only if that
    terminal is this one. A job that does not name a terminal is ambiguous and
    is claimed ONLY when this broker/account has a single configured terminal —
    that is the shape a single-terminal install writes, and what every job
    written before the instance field existed looks like. In a multi-clone
    install, an un-instanced job belongs to no clone in particular, and claiming
    it here would reproduce the cross-terminal failure this sweep exists to
    prevent.
    """
    broker = job.get("broker")
    if broker is not None and broker != BROKER:
        return False
    account = job.get("account")
    if account is not None and account != ACCOUNT:
        return False
    instance = job.get("instance")
    if instance is not None:
        return normalize_instance(instance) == normalize_instance(INSTANCE)
    return _configured_terminals(BROKER, ACCOUNT) <= 1


def sweep_orphans(lookback_seconds: int | None = None) -> int:
    """Mark this terminal's queued/running jobs on disk as failed.

    Called at API startup. Only state files touched within the last
    ``lookback_seconds`` are considered: a live job rewrites its file on every
    state transition and a run is bounded by its timeout, so a file untouched
    longer than the window cannot be an active run. Sweeping the whole history
    parsed tens of thousands of files on every boot — and because every backtest
    API on a VM shares the same job directory, that full scan repeated once per
    process. Returns the number of jobs swept.

    Only jobs belonging to THIS terminal are swept. The directory is shared, so
    sweeping everything meant restarting one terminal's API failed every other
    terminal's in-flight backtest with "API restarted before completion" —
    silently, and while those runs went on to finish perfectly well.
    """
    if lookback_seconds is None:
        lookback_seconds = SWEEP_LOOKBACK_SECONDS
    if not os.path.isdir(BACKTEST_JOB_DIR):
        return 0
    cutoff = time.time() - lookback_seconds
    recent = []
    try:
        with os.scandir(BACKTEST_JOB_DIR) as entries:
            for entry in entries:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                recent.append(entry.name)
    except OSError:
        return 0
    swept = 0
    for name in sorted(recent):
        path = os.path.join(BACKTEST_JOB_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                job = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("backtest sweep: cannot read %s: %s", name, exc)
            continue
        if job.get("status") not in ACTIVE_STATUSES:
            continue
        if not owns_job(job):
            continue
        job["status"] = "failed"
        job["error"] = "API restarted before completion"
        job["finishedAt"] = now_iso()
        try:
            _atomic_write(path, job)
        except OSError as exc:
            log.warning("backtest sweep: cannot write %s: %s", name, exc)
            continue
        swept += 1
        log.info("backtest sweep: marked %s as failed (was %s)", job.get("jobId"), name)
    return swept


def _remove_job_state(json_path: str, job_id: str | None) -> None:
    targets = [json_path]
    if job_id and _JOB_ID_RE.fullmatch(job_id):
        targets.append(os.path.join(BACKTEST_JOB_DIR, job_id))
    elif job_id:
        log.warning("backtest prune: refusing to remove staging for suspicious jobId %r", job_id)
    for target in targets:
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except OSError as exc:
            log.warning("backtest prune: cannot remove %s: %s", target, exc)


def _touch(path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(int(time.time())))
    except OSError as exc:
        log.warning("backtest prune: cannot write marker %s: %s", path, exc)


def prune_old_jobs(max_age_seconds: int | None = None) -> int:
    """Retire terminal (completed/failed) jobs older than the retention window.

    Removes the state file and the per-job staging dir (staged .ex5/.set,
    normalized.ini, tester.ini, run.log). Nothing reads old staging: the report
    artifact lives in the terminal's own Reports dir, /backtest/<id>/log reads
    run.log only as a best-effort fallback, and the backend archives both report
    and log into its own data dir right after each job. Bounds the shared job
    dir so startup stays cheap as jobs accumulate. Returns the number pruned.
    """
    if max_age_seconds is None:
        max_age_seconds = JOB_RETENTION_SECONDS
    if not os.path.isdir(BACKTEST_JOB_DIR):
        return 0
    marker = os.path.join(BACKTEST_JOB_DIR, PRUNE_MARKER_NAME)
    try:
        if time.time() - os.path.getmtime(marker) < PRUNE_INTERVAL_SECONDS:
            return 0
    except OSError:
        pass
    try:
        with os.scandir(BACKTEST_JOB_DIR) as entries:
            listed = list(entries)
    except OSError:
        return 0
    if len(listed) < PRUNE_MIN_ENTRIES:
        return 0
    cutoff = time.time() - max_age_seconds
    pruned = 0
    for entry in listed:
        if entry.name == PRUNE_MARKER_NAME or not entry.name.endswith(".json"):
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
        except OSError:
            continue
        try:
            with open(entry.path, "r", encoding="utf-8") as handle:
                job = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("backtest prune: cannot read %s: %s", entry.name, exc)
            continue
        if job.get("status") in ACTIVE_STATUSES:
            continue
        _remove_job_state(entry.path, job.get("jobId"))
        pruned += 1
    _touch(marker)
    if pruned:
        log.info(
            "backtest prune: retired %d terminal job(s) older than %ds",
            pruned,
            max_age_seconds,
        )
    return pruned


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
