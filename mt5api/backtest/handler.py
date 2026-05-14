"""Backtest Flask views: build-ini, run, status, report, log.

Run flow:
  1. Validate multipart inputs (ini text + expert/.set bytes or names).
  2. Inject [Common] credentials from config.yaml.
  3. Stage files under logs/backtest-jobs/<jobId>/.
  4. Copy expert into MQL5\\Experts\\Uploaded\\ and set into
     MQL5\\Profiles\\Tester\\, namespacing the .set with the jobId so
     concurrent submissions cannot clobber each other.
  5. Write the final INI as UTF-16-LE+BOM (MT5 silently rejects [Tester]
     Login under UTF-8 — verified during the prior backtester branch
     work).
  6. Spawn a worker thread that holds RUN_LOCK while terminal64.exe runs.
"""
from __future__ import annotations

import configparser
import io
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

from flask import Response, abort, jsonify, request, send_file

from mt5api.backtest import ini_builder, jobs
from mt5api.config import (
    ACCOUNT,
    BROKER,
    LOG_DIR,
    TERMINAL_DIR,
    TERMINAL_PATH,
    SYMBOL_SUFFIX,
    SYMBOL_SUFFIX_CONFIGURED,
    ASSETS_DIR,
    BACKTEST_TIMEOUT_SECONDS,
    BACKTEST_JOB_DIR,
    load_yaml_config,
    parse_duration_to_seconds,
)
from mt5api.logger import log

RUN_LOCK = threading.Lock()
DIAGNOSTIC_TAIL_CHARS = 4000


# ── INI builder route ───────────────────────────────────────────────


def build_ini_route():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    try:
        ini_text = ini_builder.build_ini(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return Response(ini_text, mimetype="text/plain")


# ── Helpers ─────────────────────────────────────────────────────────


def _load_account_config():
    data = load_yaml_config()
    accounts = (data.get("accounts") or {}).get(BROKER)
    if not isinstance(accounts, dict):
        raise ValueError(f"Broker not configured in config.yaml: {BROKER}")
    creds = accounts.get(ACCOUNT)
    if not isinstance(creds, dict):
        raise ValueError(f"Account not configured in config.yaml: {BROKER}/{ACCOUNT}")
    for field in ("login", "password", "server"):
        if not creds.get(field):
            raise ValueError(f"Missing account field '{field}' for {BROKER}/{ACCOUNT}")
    return creds


def _safe_basename(name, field):
    name = (name or "").strip()
    if not name:
        return ""
    if name != os.path.basename(name) or name in ("..", "."):
        raise ValueError(f"{field} must be a filename, not a path")
    return name


def _read_submission(upload, asset_name, asset_subdir, field, *, required, required_ext):
    """Resolve an expert/set input from either an upload or a host-managed name."""
    if upload is not None and upload.filename:
        filename = _safe_basename(upload.filename, field)
        data = upload.stream.read()
    else:
        filename = _safe_basename(asset_name, field)
        if not filename:
            if required:
                raise ValueError(f"Missing backtest input: {field}")
            return "", b""
        path = os.path.join(ASSETS_DIR, asset_subdir, filename)
        if not os.path.isfile(path):
            raise ValueError(f"{field} asset not found: {filename}")
        with open(path, "rb") as handle:
            data = handle.read()
    if required_ext and not filename.lower().endswith(required_ext):
        raise ValueError(f"{field} must be a {required_ext} file")
    return filename, data


def _parse_ini(text):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(text)
    if "Tester" not in parser:
        raise ValueError("INI missing [Tester] section")
    return parser


def _override_credentials(parser, creds):
    if "Common" not in parser:
        parser["Common"] = {}
    common = parser["Common"]
    common["Login"] = str(creds["login"])
    common["Password"] = str(creds["password"])
    common["Server"] = str(creds["server"])


def _ensure_report_path(parser):
    tester = parser["Tester"]
    raw = tester.get("Report", "").strip()
    name = raw.split("\\")[-1].split("/")[-1].strip() or f"backtest-{uuid.uuid4().hex}.htm"
    if not name.lower().endswith((".htm", ".html")):
        name += ".htm"
    tester["Report"] = f"Reports\\{name}"
    tester["ReplaceReport"] = "1"
    tester["ShutdownTerminal"] = "1"
    return name


def _normalize_expert(parser, expert_filename):
    base = expert_filename
    if base.lower().endswith(".ex5"):
        base = base[:-4]
    parser["Tester"]["Expert"] = f"Uploaded\\{base}"


def _normalize_set(parser, set_filename):
    if set_filename:
        parser["Tester"]["ExpertParameters"] = set_filename
    else:
        parser["Tester"].pop("ExpertParameters", None)


def _normalize_symbol(parser):
    tester = parser["Tester"]
    symbol = tester.get("Symbol", "").strip()
    if not symbol:
        return

    suffix = SYMBOL_SUFFIX if SYMBOL_SUFFIX_CONFIGURED else ""
    if not suffix:
        return

    if symbol.endswith(suffix):
        return

    remapped = f"{symbol}{suffix}"
    tester["Symbol"] = remapped
    log.info(
        "backtest symbol remap broker=%s account=%s %s -> %s",
        BROKER,
        ACCOUNT,
        symbol,
        remapped,
    )


def _serialize_ini(parser):
    buffer = io.StringIO()
    parser.write(buffer, space_around_delimiters=False)
    return buffer.getvalue()


def _write_utf16_ini(parser, path):
    text = _serialize_ini(parser).replace("\n", "\r\n")
    with open(path, "wb") as handle:
        handle.write(b"\xff\xfe")
        handle.write(text.encode("utf-16-le"))


def _read_text_best_effort(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _tail(text, limit=DIAGNOSTIC_TAIL_CHARS):
    if not text:
        return ""
    return text if len(text) <= limit else text[-limit:]


def _tail_terminal_log(lines=20):
    log_dir = os.path.join(TERMINAL_DIR, "logs")
    if not os.path.isdir(log_dir):
        return ""

    try:
        candidates = sorted(
            file_name for file_name in os.listdir(log_dir) if file_name.endswith(".log")
        )
    except OSError:
        return ""

    if not candidates:
        return ""

    latest_path = os.path.join(log_dir, candidates[-1])
    try:
        with open(latest_path, "r", encoding="utf-16-le", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return ""

    tail_lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not tail_lines:
        return ""
    return "\n".join(tail_lines[-lines:])


# ── Submit route ────────────────────────────────────────────────────


def run_backtest():
    if not os.path.exists(TERMINAL_PATH):
        return jsonify({"error": f"Terminal not found: {TERMINAL_PATH}"}), 500

    ini_upload = request.files.get("ini")
    if ini_upload is None or not ini_upload.filename:
        return jsonify({"error": "Missing form file: ini"}), 400

    try:
        ini_text = ini_upload.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "INI must be UTF-8 text"}), 400

    try:
        timeout_value = (request.form.get("timeout") or "").strip()
        timeout_seconds = (
            parse_duration_to_seconds(timeout_value)
            if timeout_value
            else BACKTEST_TIMEOUT_SECONDS
        )
        expert_filename, expert_bytes = _read_submission(
            request.files.get("expert"),
            request.form.get("expert_name", ""),
            "experts",
            "expert",
            required=True,
            required_ext=".ex5",
        )
        set_filename, set_bytes = _read_submission(
            request.files.get("set"),
            request.form.get("set_name", ""),
            "sets",
            "set",
            required=False,
            required_ext=".set",
        )
        parser = _parse_ini(ini_text)
        creds = _load_account_config()
        _override_credentials(parser, creds)
        _normalize_symbol(parser)
        _normalize_expert(parser, expert_filename)
        report_name = _ensure_report_path(parser)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = uuid.uuid4().hex
    # Namespace the .set so concurrent jobs cannot clobber the same file in
    # MQL5\Profiles\Tester\.
    staged_set_filename = f"{job_id}__{set_filename}" if set_filename else ""
    _normalize_set(parser, staged_set_filename)

    stage_dir = os.path.join(BACKTEST_JOB_DIR, job_id)
    os.makedirs(stage_dir, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    staged_expert_path = os.path.join(stage_dir, expert_filename)
    with open(staged_expert_path, "wb") as handle:
        handle.write(expert_bytes)
    staged_set_path = ""
    if set_filename:
        staged_set_path = os.path.join(stage_dir, staged_set_filename)
        with open(staged_set_path, "wb") as handle:
            handle.write(set_bytes)

    # Save the human-readable normalized INI for debugging.
    debug_ini_path = os.path.join(stage_dir, "normalized.ini")
    with open(debug_ini_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_serialize_ini(parser))

    report_path = os.path.join(TERMINAL_DIR, "Reports", report_name)
    log_path = os.path.join(stage_dir, "run.log")

    job = {
        "jobId": job_id,
        "status": "queued",
        "broker": BROKER,
        "account": ACCOUNT,
        "submittedAt": jobs.now_iso(),
        "startedAt": None,
        "finishedAt": None,
        "durationSeconds": None,
        "reportName": report_name,
        "reportPath": report_path,
        "logPath": log_path,
        "debugIniPath": debug_ini_path,
        "stageDir": stage_dir,
        "expertFilename": expert_filename,
        "setFilename": set_filename,
        "stagedSetFilename": staged_set_filename,
        "stagedExpertPath": staged_expert_path,
        "stagedSetPath": staged_set_path,
        "exitCode": None,
        "error": None,
        "summary": None,
        "timeoutSeconds": timeout_seconds,
    }
    jobs.store_job(job)

    threading.Thread(target=_execute_job, args=(job_id,), daemon=True).start()

    response = jsonify(jobs.public_payload(job))
    response.status_code = 202
    response.headers["Retry-After"] = str(jobs.POLL_AFTER_SECONDS)
    return response


# ── Worker ──────────────────────────────────────────────────────────


def _execute_job(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return

    reports_dir = os.path.join(TERMINAL_DIR, "Reports")
    experts_dir = os.path.join(TERMINAL_DIR, "MQL5", "Experts", "Uploaded")
    sets_dir = os.path.join(TERMINAL_DIR, "MQL5", "Profiles", "Tester")

    try:
        os.makedirs(reports_dir, exist_ok=True)
        os.makedirs(experts_dir, exist_ok=True)
        os.makedirs(sets_dir, exist_ok=True)
    except OSError as exc:
        jobs.update_job(
            job_id,
            status="failed",
            error=f"Cannot prepare terminal directories: {exc}",
            finishedAt=jobs.now_iso(),
        )
        return

    expert_dest = os.path.join(experts_dir, job["expertFilename"])
    set_dest = (
        os.path.join(sets_dir, job["stagedSetFilename"])
        if job["stagedSetFilename"]
        else ""
    )
    if os.path.exists(job["reportPath"]):
        try:
            os.remove(job["reportPath"])
        except OSError:
            pass

    started_at = jobs.now_iso()
    start_time = time.time()
    with RUN_LOCK:
        jobs.update_job(job_id, status="running", startedAt=started_at)
        try:
            shutil.copyfile(job["stagedExpertPath"], expert_dest)
            if set_dest:
                shutil.copyfile(job["stagedSetPath"], set_dest)
            parser = _parse_ini(_read_text_best_effort(job["debugIniPath"]))
            ini_path = os.path.join(job["stageDir"], "tester.ini")
            _write_utf16_ini(parser, ini_path)

            cmd = [TERMINAL_PATH, "/portable", f"/config:{ini_path}"]
            log.info(
                "backtest start broker=%s account=%s job=%s report=%s",
                BROKER, ACCOUNT, job_id, job["reportName"],
            )
            with open(job["logPath"], "w", encoding="utf-8") as log_handle:
                try:
                    result = subprocess.run(
                        cmd,
                        cwd=TERMINAL_DIR,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        timeout=job["timeoutSeconds"],
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    duration = round(time.time() - start_time, 3)
                    jobs.update_job(
                        job_id,
                        status="failed",
                        error=f"Backtest timed out after {job['timeoutSeconds']}s",
                        durationSeconds=duration,
                        finishedAt=jobs.now_iso(),
                    )
                    return
        except Exception as exc:
            log.exception("backtest crashed broker=%s account=%s job=%s", BROKER, ACCOUNT, job_id)
            jobs.update_job(
                job_id,
                status="failed",
                error=f"Backtest crashed: {exc}",
                durationSeconds=round(time.time() - start_time, 3),
                finishedAt=jobs.now_iso(),
            )
            return
        finally:
            # Clean up the per-job set file copy so MQL5\Profiles\Tester\ does
            # not accumulate junk over time. Errors are non-fatal.
            if set_dest and os.path.exists(set_dest):
                try:
                    os.remove(set_dest)
                except OSError:
                    pass

    duration = round(time.time() - start_time, 3)
    if result.returncode != 0:
        terminal_tail = _tail(_tail_terminal_log())
        error = f"terminal64.exe exited with code {result.returncode}"
        if terminal_tail:
            error = f"{error} | terminal log tail: {terminal_tail}"
        jobs.update_job(
            job_id,
            status="failed",
            error=error,
            exitCode=result.returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return

    if not os.path.exists(job["reportPath"]):
        jobs.update_job(
            job_id,
            status="failed",
            error="Report not generated",
            exitCode=result.returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return

    report_html = _read_text_best_effort(job["reportPath"])
    summary = jobs.parse_report_summary(report_html)
    if jobs.is_empty_backtest_summary(summary):
        terminal_tail = _tail(_tail_terminal_log())
        error = "Backtest produced empty report (Bars=0, Ticks=0, Symbols=0)"
        if terminal_tail:
            error = f"{error} | terminal log tail: {terminal_tail}"
        jobs.update_job(
            job_id,
            status="failed",
            error=error,
            exitCode=result.returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
            summary=summary,
        )
        return
    jobs.update_job(
        job_id,
        status="completed",
        exitCode=result.returncode,
        durationSeconds=duration,
        finishedAt=jobs.now_iso(),
        summary=summary,
    )
    log.info("backtest done broker=%s account=%s job=%s duration=%.1fs", BROKER, ACCOUNT, job_id, duration)


# ── Status / artifacts ──────────────────────────────────────────────


def get_status(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    return jsonify(jobs.public_payload(job))


def get_report(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    path = job.get("reportPath")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Report not available yet"}), 404
    return send_file(path, mimetype="text/html", as_attachment=False, download_name=job["reportName"])


def get_log(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    path = job.get("logPath")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Log not available yet"}), 404
    return send_file(path, mimetype="text/plain", as_attachment=False, download_name=f"{job_id}.log")
