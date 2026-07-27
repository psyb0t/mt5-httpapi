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

import base64
import configparser
import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import zipfile

from flask import Response, abort, jsonify, request, send_file

from mt5api.backtest import (
    cache_parser,
    ini_builder,
    jobs,
    materialization,
    optimization_parser,
    set_builder,
)
from mt5api.config import (
    ACCOUNT,
    BROKER,
    COMMON_FILES_DIR,
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
ACTIVE_PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES = {}
DIAGNOSTIC_TAIL_CHARS = 4000
DEFAULT_TOP_PASSES = 50
MAX_TOP_PASSES = 500
OWNED_PROCESS_TERMINATE_SECONDS = 15


class ProjectJobCanceled(Exception):
    pass


# ── INI builder route ───────────────────────────────────────────────


def build_ini_route():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    try:
        ini_text = ini_builder.build_ini(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return Response(ini_text, mimetype="text/plain")


def build_set_route():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    try:
        set_text = set_builder.build_set(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return Response(set_text, mimetype="text/plain")


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


def _optimization_type(parser):
    raw = parser["Tester"].get("Optimization", "0").strip() or "0"
    try:
        optimization_type = int(raw)
    except ValueError as exc:
        raise ValueError("Tester Optimization must be an integer 0..3") from exc
    if optimization_type not in (0, 1, 2, 3):
        raise ValueError("Tester Optimization must be 0..3")
    return optimization_type


def _ensure_report_path(parser):
    tester = parser["Tester"]
    raw = tester.get("Report", "").strip()
    optimization_type = _optimization_type(parser)
    default_name = (
        f"optimization-{uuid.uuid4().hex}.xml"
        if optimization_type
        else f"backtest-{uuid.uuid4().hex}.htm"
    )
    name = raw.split("\\")[-1].split("/")[-1].strip() or default_name
    if optimization_type:
        if not name.lower().endswith(".xml"):
            name += ".xml"
    elif not name.lower().endswith((".htm", ".html")):
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


def _parse_top_passes(raw_value):
    raw_value = (raw_value or "").strip()
    if not raw_value:
        return DEFAULT_TOP_PASSES
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("topPasses must be an integer 1..500") from exc
    if value < 1 or value > MAX_TOP_PASSES:
        raise ValueError("topPasses must be 1..500")
    return value


def _save_upload(upload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    upload.save(path)


def _canonical_ini_path(value, *, remove_ex5=False):
    canonical = (value or "").strip().replace("/", "\\")
    while "\\\\" in canonical:
        canonical = canonical.replace("\\\\", "\\")
    if remove_ex5 and canonical.lower().endswith(".ex5"):
        canonical = canonical[:-4]
    return canonical


def _validate_project_ini_contract(parser, project):
    tester = parser["Tester"]
    actual_expert = _canonical_ini_path(tester.get("Expert", ""), remove_ex5=True)
    required_expert = _canonical_ini_path(project.expert_ini_value, remove_ex5=True)
    if actual_expert.casefold() != required_expert.casefold():
        raise materialization.MaterializationError(
            "ini_expert_manifest_mismatch",
            "INI [Tester].Expert does not identify expertRelativePath from the project manifest",
            details={"actual": actual_expert, "required": required_expert},
        )
    actual_preset = _canonical_ini_path(tester.get("ExpertParameters", ""))
    required_preset = _canonical_ini_path(project.preset_ini_value)
    if actual_preset.casefold() != required_preset.casefold():
        raise materialization.MaterializationError(
            "ini_preset_manifest_mismatch",
            "INI [Tester].ExpertParameters does not identify presetRelativePath from the project manifest",
            details={"actual": actual_preset, "required": required_preset},
        )
    tester["Expert"] = required_expert
    if required_preset:
        tester["ExpertParameters"] = required_preset
    else:
        tester.pop("ExpertParameters", None)


def _write_json_evidence(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


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

    project_manifest_upload = request.files.get("project_manifest")
    project_bundle_upload = request.files.get("project_bundle")
    project_mode = bool(
        (project_manifest_upload is not None and project_manifest_upload.filename)
        or (project_bundle_upload is not None and project_bundle_upload.filename)
    )
    if project_mode and not (
        project_manifest_upload is not None
        and project_manifest_upload.filename
        and project_bundle_upload is not None
        and project_bundle_upload.filename
    ):
        return jsonify({
            "error": "project_manifest and project_bundle are both required for project mode",
            "projectManifestPresent": bool(
                project_manifest_upload is not None and project_manifest_upload.filename
            ),
            "projectBundlePresent": bool(
                project_bundle_upload is not None and project_bundle_upload.filename
            ),
        }), 400

    job_id = uuid.uuid4().hex
    stage_dir = os.path.join(BACKTEST_JOB_DIR, job_id)
    os.makedirs(stage_dir, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    submitted_ini_path = os.path.join(stage_dir, "submitted.ini")
    with open(submitted_ini_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(ini_text)
    project_manifest_path = os.path.join(stage_dir, "project-manifest.json")
    project_bundle_path = os.path.join(stage_dir, "project-bundle.zip")
    project = None

    try:
        timeout_value = (request.form.get("timeout") or "").strip()
        top_passes = _parse_top_passes(request.form.get("topPasses"))
        timeout_seconds = (
            parse_duration_to_seconds(timeout_value)
            if timeout_value
            else BACKTEST_TIMEOUT_SECONDS
        )
        parser = _parse_ini(ini_text)
        creds = _load_account_config()
        _override_credentials(parser, creds)
        _normalize_symbol(parser)
        if project_mode:
            _save_upload(project_manifest_upload, project_manifest_path)
            _save_upload(project_bundle_upload, project_bundle_path)
            project = materialization.ProjectMaterialization(
                project_manifest_path,
                project_bundle_path,
                stage_dir,
                TERMINAL_DIR,
                COMMON_FILES_DIR,
            )
            _validate_project_ini_contract(parser, project)
            expert_filename = os.path.basename(project.manifest.expert_relative_path)
            set_filename = (
                os.path.basename(project.manifest.preset_relative_path)
                if project.manifest.preset_relative_path
                else ""
            )
            expert_bytes = b""
            set_bytes = b""
        else:
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
            _normalize_expert(parser, expert_filename)
        optimization_type = _optimization_type(parser)
        report_name = _ensure_report_path(parser)
    except (ValueError, OSError) as exc:
        if project_mode:
            error = (
                exc.as_dict()
                if isinstance(exc, materialization.MaterializationError)
                else {"code": type(exc).__name__, "message": str(exc)}
            )
            submission_error_path = os.path.join(stage_dir, "submission-error.json")
            _write_json_evidence(submission_error_path, {
                "jobId": job_id,
                "status": "failed",
                "failedAt": jobs.now_iso(),
                "error": error,
                "projectManifestPath": (
                    project_manifest_path if os.path.exists(project_manifest_path) else None
                ),
                "projectBundlePath": (
                    project_bundle_path if os.path.exists(project_bundle_path) else None
                ),
                "submittedIniPath": submitted_ini_path,
            })
            failed_job = {
                "jobId": job_id,
                "status": "failed",
                "broker": BROKER,
                "account": ACCOUNT,
                "submittedAt": jobs.now_iso(),
                "startedAt": None,
                "finishedAt": jobs.now_iso(),
                "durationSeconds": None,
                "reportName": None,
                "reportPath": None,
                "logPath": None,
                "stageDir": stage_dir,
                "submittedIniPath": submitted_ini_path,
                "projectMode": True,
                "projectId": project.manifest.project_id if project else None,
                "externalRunId": project.manifest.run_id if project else None,
                "planFingerprint": project.manifest.plan_fingerprint if project else None,
                "projectManifestPath": (
                    project_manifest_path if os.path.exists(project_manifest_path) else None
                ),
                "projectBundlePath": (
                    project_bundle_path if os.path.exists(project_bundle_path) else None
                ),
                "materializationAuditPath": (
                    project.audit_path
                    if project and os.path.exists(project.audit_path)
                    else None
                ),
                "submissionErrorPath": submission_error_path,
                "materializationStatus": "validation_failed",
                "cancelRequested": False,
                "exitCode": None,
                "error": error,
                "summary": None,
                "optimizationType": 0,
                "optimizationResults": None,
                "optimizationCache": None,
            }
            jobs.store_job(failed_job)
            return jsonify({**jobs.public_payload(failed_job), "error": error}), 400
        return jsonify({"error": str(exc)}), 400

    # Namespace the .set so concurrent jobs cannot clobber the same file in
    # MQL5\Profiles\Tester\.
    staged_set_filename = (
        ""
        if project_mode
        else (f"{job_id}__{set_filename}" if set_filename else "")
    )
    if not project_mode:
        _normalize_set(parser, staged_set_filename)

    staged_expert_path = ""
    staged_set_path = ""
    if not project_mode:
        staged_expert_path = os.path.join(stage_dir, expert_filename)
        with open(staged_expert_path, "wb") as handle:
            handle.write(expert_bytes)
        if set_filename:
            staged_set_path = os.path.join(stage_dir, staged_set_filename)
            with open(staged_set_path, "wb") as handle:
                handle.write(set_bytes)

    # Save the human-readable normalized INI for debugging.
    debug_ini_path = os.path.join(stage_dir, "normalized.ini")
    with open(debug_ini_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_serialize_ini(parser))

    terminal_report_path = os.path.join(TERMINAL_DIR, "Reports", report_name)
    report_path = (
        os.path.join(stage_dir, report_name) if project_mode else terminal_report_path
    )
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
        "terminalReportPath": terminal_report_path,
        "logPath": log_path,
        "debugIniPath": debug_ini_path,
        "submittedIniPath": submitted_ini_path,
        "stageDir": stage_dir,
        "expertFilename": expert_filename,
        "setFilename": set_filename,
        "stagedSetFilename": staged_set_filename,
        "stagedExpertPath": staged_expert_path,
        "stagedSetPath": staged_set_path,
        "exitCode": None,
        "error": None,
        "summary": None,
        "optimizationType": optimization_type,
        "optimizationResults": None,
        "optimizationCache": None,
        "topPasses": top_passes,
        "timeoutSeconds": timeout_seconds,
        "projectMode": project_mode,
        "projectId": project.manifest.project_id if project else None,
        "externalRunId": project.manifest.run_id if project else None,
        "planFingerprint": project.manifest.plan_fingerprint if project else None,
        "projectManifestPath": project_manifest_path if project_mode else None,
        "projectBundlePath": project_bundle_path if project_mode else None,
        "materializationAuditPath": project.audit_path if project else None,
        "outputManifestPath": project.output_manifest_path if project else None,
        "outputArtifactsPath": project.output_archive_path if project else None,
        "executionManifestPath": (
            os.path.join(stage_dir, "execution-manifest.json") if project_mode else None
        ),
        "logDeltaManifestPath": (
            os.path.join(stage_dir, "log-deltas-manifest.json") if project_mode else None
        ),
        "logBaselinePath": (
            os.path.join(stage_dir, "log-baseline.json") if project_mode else None
        ),
        "logDeltaArtifactsPath": (
            os.path.join(stage_dir, "log-deltas.zip") if project_mode else None
        ),
        "materializationStatus": "validated" if project_mode else None,
        "cancelRequested": False,
    }
    jobs.store_job(job)

    threading.Thread(target=_execute_job, args=(job_id,), daemon=True).start()

    response = jsonify(jobs.public_payload(job))
    response.status_code = 202
    response.headers["Retry-After"] = str(
        jobs.PROJECT_POLL_AFTER_SECONDS if project_mode else jobs.POLL_AFTER_SECONDS
    )
    return response


# ── Worker ──────────────────────────────────────────────────────────


def _execute_job(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return
    if job.get("projectMode"):
        _execute_project_job(job_id)
        return
    _execute_legacy_job(job_id)


def _log_inventory():
    inventory = {}
    for scope, log_dir in (
        ("terminal", os.path.join(TERMINAL_DIR, "logs")),
        ("tester", os.path.join(TERMINAL_DIR, "Tester", "logs")),
    ):
        if not os.path.isdir(log_dir):
            continue
        for current_root, _, file_names in os.walk(log_dir):
            for file_name in sorted(file_names):
                if not file_name.lower().endswith(".log"):
                    continue
                path = os.path.join(current_root, file_name)
                if not os.path.isfile(path):
                    continue
                relative = os.path.relpath(path, log_dir).replace(os.sep, "/")
                inventory[f"{scope}/{relative}"] = {
                    "scope": scope,
                    "relativePath": relative,
                    "path": path,
                    "size": os.path.getsize(path),
                    "sha256": materialization.sha256_file(path),
                }
    return dict(sorted(inventory.items(), key=lambda item: item[0].casefold()))


def _hash_prefix(path, length):
    digest = hashlib.sha256()
    remaining = length
    with open(path, "rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest(), length - remaining


def _copy_file_range(source_path, destination_path, offset):
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    with open(source_path, "rb") as source, open(destination_path, "wb") as destination:
        source.seek(offset)
        shutil.copyfileobj(source, destination, length=1024 * 1024)


def _capture_log_deltas(job, baseline):
    current = _log_inventory()
    records = []
    delta_root = os.path.join(job["stageDir"], "log-deltas")
    for key in sorted(set(baseline) | set(current), key=lambda value: value.casefold()):
        before = baseline.get(key)
        after = current.get(key)
        classification = None
        offset = 0
        if before is None:
            classification = "created"
        elif after is None:
            classification = "missing_after_run"
        elif before["size"] == after["size"] and before["sha256"] == after["sha256"]:
            classification = "unchanged"
        elif after["size"] >= before["size"]:
            prefix_sha256, prefix_size = _hash_prefix(after["path"], before["size"])
            if prefix_size == before["size"] and prefix_sha256 == before["sha256"]:
                classification = "appended"
                offset = before["size"]
            else:
                classification = "replaced"
        else:
            classification = "truncated_or_replaced"

        delta_path = None
        delta_size = None
        delta_sha256 = None
        if after is not None and classification not in ("unchanged", "missing_after_run"):
            delta_path = os.path.join(delta_root, *key.split("/"))
            _copy_file_range(after["path"], delta_path, offset)
            delta_size = os.path.getsize(delta_path)
            delta_sha256 = materialization.sha256_file(delta_path)
        archive_entry = f"deltas/{key}" if delta_path else None
        records.append({
            "key": key,
            "classification": classification,
            "before": before,
            "after": after,
            "capturedOffset": offset if delta_path else None,
            "deltaPath": delta_path,
            "deltaSize": delta_size,
            "deltaSha256": delta_sha256,
            "archiveEntry": archive_entry,
        })
    with zipfile.ZipFile(
        job["logDeltaArtifactsPath"],
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for record in records:
            if record["deltaPath"]:
                archive.write(record["deltaPath"], arcname=record["archiveEntry"])
    payload = {
        "schemaVersion": "MT5-CLI-REMOTE-LOG-DELTAS-001",
        "jobId": job["jobId"],
        "projectId": job.get("projectId"),
        "externalRunId": job.get("externalRunId"),
        "capturedAt": jobs.now_iso(),
        "archivePath": job["logDeltaArtifactsPath"],
        "archiveSize": os.path.getsize(job["logDeltaArtifactsPath"]),
        "archiveSha256": materialization.sha256_file(job["logDeltaArtifactsPath"]),
        "files": records,
    }
    _write_json_evidence(job["logDeltaManifestPath"], payload)
    return payload


def _prepare_report_backup(job):
    paths = [job["terminalReportPath"]]
    symbols_path = f"{os.path.splitext(job['terminalReportPath'])[0]}.symbols.xml"
    if os.path.normcase(symbols_path) != os.path.normcase(paths[0]):
        paths.append(symbols_path)
    evidence = []
    backup_root = os.path.join(job["stageDir"], "report-backups")
    for index, path in enumerate(paths):
        present = os.path.isfile(path)
        backup_path = os.path.join(backup_root, f"{index:02d}-{os.path.basename(path)}")
        record = {
            "path": path,
            "present": present,
            "size": os.path.getsize(path) if present else None,
            "sha256": materialization.sha256_file(path) if present else None,
            "backupPath": backup_path if present else None,
        }
        if present:
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            shutil.copyfile(path, backup_path)
            if materialization.sha256_file(backup_path) != record["sha256"]:
                raise materialization.MaterializationError(
                    "report_backup_integrity_mismatch",
                    "Existing terminal report backup failed SHA-256 verification",
                    details=record,
                )
        if os.path.exists(path):
            os.remove(path)
        evidence.append(record)
    return evidence


def _collect_project_report(job):
    source = job["terminalReportPath"]
    report_name = job["reportName"]
    if not os.path.isfile(source) and job.get("optimizationType") == 3:
        symbols_path = f"{os.path.splitext(source)[0]}.symbols.xml"
        if os.path.isfile(symbols_path):
            source = symbols_path
            report_name = os.path.basename(symbols_path)
    if not os.path.isfile(source):
        return None
    destination = os.path.join(job["stageDir"], report_name)
    shutil.copyfile(source, destination)
    source_sha256 = materialization.sha256_file(source)
    destination_sha256 = materialization.sha256_file(destination)
    if source_sha256 != destination_sha256:
        raise materialization.MaterializationError(
            "report_capture_integrity_mismatch",
            "Captured report differs from the terminal report",
            details={
                "source": source,
                "sourceSha256": source_sha256,
                "destination": destination,
                "destinationSha256": destination_sha256,
            },
        )
    jobs.update_job(job["jobId"], reportPath=destination, reportName=report_name)
    job["reportPath"] = destination
    job["reportName"] = report_name
    return {
        "terminalPath": source,
        "capturedPath": destination,
        "size": os.path.getsize(destination),
        "sha256": destination_sha256,
    }


def _restore_report_backup(evidence):
    failures = []
    for record in evidence:
        try:
            if os.path.exists(record["path"]):
                os.remove(record["path"])
            if record["present"]:
                os.makedirs(os.path.dirname(record["path"]), exist_ok=True)
                shutil.copyfile(record["backupPath"], record["path"])
                restored_sha256 = materialization.sha256_file(record["path"])
                if restored_sha256 != record["sha256"]:
                    raise materialization.MaterializationError(
                        "report_restore_integrity_mismatch",
                        "Restored terminal report differs from its backup",
                        details={**record, "restoredSha256": restored_sha256},
                    )
            record["restored"] = True
        except Exception as exc:
            error = exc.as_dict() if isinstance(
                exc, materialization.MaterializationError
            ) else {"code": type(exc).__name__, "message": str(exc)}
            record["restored"] = False
            record["restoreError"] = error
            failures.append({"path": record["path"], **error})
    if failures:
        raise materialization.MaterializationError(
            "report_restore_failed",
            "One or more terminal report paths could not be restored",
            details=failures,
        )


def _terminate_owned_process(process):
    actions = []
    if process.poll() is not None:
        return actions
    process.terminate()
    actions.append({"action": "terminate", "at": jobs.now_iso()})
    try:
        process.wait(timeout=OWNED_PROCESS_TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        actions.append({"action": "kill", "at": jobs.now_iso()})
        process.wait(timeout=OWNED_PROCESS_TERMINATE_SECONDS)
    return actions


def _execute_project_job(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return
    start_time = time.time()
    execution = {
        "schemaVersion": "MT5-CLI-REMOTE-EXECUTION-001",
        "jobId": job_id,
        "projectId": job.get("projectId"),
        "externalRunId": job.get("externalRunId"),
        "planFingerprint": job.get("planFingerprint"),
        "broker": BROKER,
        "account": ACCOUNT,
        "submittedAt": job.get("submittedAt"),
        "startedAt": None,
        "finishedAt": None,
        "command": None,
        "cwd": TERMINAL_DIR,
        "pid": None,
        "exitCode": None,
        "stdoutPath": job.get("logPath"),
        "stderr": "combined_with_stdout",
        "timeoutSeconds": job.get("timeoutSeconds"),
        "timedOut": False,
        "cancelRequested": False,
        "terminationActions": [],
        "reportBackups": [],
        "report": None,
        "logDeltaManifestPath": job.get("logDeltaManifestPath"),
        "materializationAuditPath": job.get("materializationAuditPath"),
        "outputManifestPath": job.get("outputManifestPath"),
        "outputArtifactsPath": job.get("outputArtifactsPath"),
        "errors": [],
    }
    _write_json_evidence(job["executionManifestPath"], execution)
    project = None
    project_applied = False
    report_backups = []
    log_baseline = {}
    log_baseline_captured = False
    result_returncode = None
    failure = None
    canceled = False

    with RUN_LOCK:
        current = jobs.load_job(job_id) or job
        if current.get("cancelRequested"):
            canceled = True
        if canceled:
            jobs.update_job(
                job_id,
                status="canceled",
                finishedAt=jobs.now_iso(),
                durationSeconds=round(time.time() - start_time, 3),
            )
            execution["cancelRequested"] = True
            execution["finishedAt"] = jobs.now_iso()
            _write_json_evidence(job["executionManifestPath"], execution)
            return

        started_at = jobs.now_iso()
        execution["startedAt"] = started_at
        jobs.update_job(job_id, status="running", startedAt=started_at)
        try:
            project = materialization.ProjectMaterialization(
                job["projectManifestPath"],
                job["projectBundlePath"],
                job["stageDir"],
                TERMINAL_DIR,
                COMMON_FILES_DIR,
            )
            jobs.update_job(
                job_id,
                materializationAuditPath=project.audit_path,
                materializationStatus="validated",
            )
            report_backups = _prepare_report_backup(job)
            execution["reportBackups"] = report_backups
            log_baseline = _log_inventory()
            log_baseline_captured = True
            _write_json_evidence(job["logBaselinePath"], {
                "schemaVersion": "MT5-CLI-REMOTE-LOG-BASELINE-001",
                "jobId": job_id,
                "projectId": job.get("projectId"),
                "externalRunId": job.get("externalRunId"),
                "capturedAt": jobs.now_iso(),
                "files": log_baseline,
            })
            project.apply()
            project_applied = True
            jobs.update_job(job_id, materializationStatus="applied")
            current = jobs.load_job(job_id) or job
            if current.get("cancelRequested"):
                raise ProjectJobCanceled("Project backtest was canceled before terminal launch")

            parser = _parse_ini(_read_text_best_effort(job["debugIniPath"]))
            _validate_project_ini_contract(parser, project)
            ini_path = os.path.join(job["stageDir"], "tester.ini")
            _write_utf16_ini(parser, ini_path)
            command = [TERMINAL_PATH, "/portable", f"/config:{ini_path}"]
            execution["command"] = command
            execution["iniPath"] = ini_path
            execution["iniSize"] = os.path.getsize(ini_path)
            execution["iniSha256"] = materialization.sha256_file(ini_path)
            _write_json_evidence(job["executionManifestPath"], execution)

            log.info(
                "project backtest start broker=%s account=%s job=%s external_run=%s project=%s report=%s",
                BROKER,
                ACCOUNT,
                job_id,
                job.get("externalRunId"),
                job.get("projectId"),
                job["reportName"],
            )
            with open(job["logPath"], "w", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=TERMINAL_DIR,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
                execution["pid"] = process.pid
                with ACTIVE_PROCESS_LOCK:
                    ACTIVE_PROCESSES[job_id] = process
                _write_json_evidence(job["executionManifestPath"], execution)
                try:
                    result_returncode = process.wait(timeout=job["timeoutSeconds"])
                except subprocess.TimeoutExpired:
                    execution["timedOut"] = True
                    execution["terminationActions"].extend(_terminate_owned_process(process))
                    result_returncode = process.returncode
                    failure = f"Backtest timed out after {job['timeoutSeconds']}s"
                finally:
                    with ACTIVE_PROCESS_LOCK:
                        if ACTIVE_PROCESSES.get(job_id) is process:
                            ACTIVE_PROCESSES.pop(job_id, None)

            current = jobs.load_job(job_id) or job
            canceled = bool(current.get("cancelRequested"))
            execution["cancelRequested"] = canceled
            execution["terminationActions"].extend(
                current.get("cancelTerminationActions") or []
            )
            execution["exitCode"] = result_returncode
            execution["report"] = _collect_project_report(job)
        except Exception as exc:
            current = jobs.load_job(job_id) or job
            canceled = bool(current.get("cancelRequested"))
            if isinstance(exc, ProjectJobCanceled) and canceled:
                execution["cancelRequested"] = True
            else:
                log.exception(
                    "project backtest crashed broker=%s account=%s job=%s",
                    BROKER,
                    ACCOUNT,
                    job_id,
                )
                error = exc.as_dict() if isinstance(
                    exc, materialization.MaterializationError
                ) else {"code": type(exc).__name__, "message": str(exc)}
                execution["errors"].append({"phase": "execute", **error})
                failure = f"Project backtest crashed: {error['message']}"
        finally:
            if log_baseline_captured:
                try:
                    _capture_log_deltas(job, log_baseline)
                except Exception as exc:
                    error = exc.as_dict() if isinstance(
                        exc, materialization.MaterializationError
                    ) else {"code": type(exc).__name__, "message": str(exc)}
                    execution["errors"].append({"phase": "capture_logs", **error})
                    failure = failure or f"Log delta capture failed: {error['message']}"
            if project is not None and project_applied:
                try:
                    project.capture_outputs()
                    jobs.update_job(
                        job_id,
                        outputManifestPath=project.output_manifest_path,
                        outputArtifactsPath=project.output_archive_path,
                    )
                except Exception as exc:
                    error = exc.as_dict() if isinstance(
                        exc, materialization.MaterializationError
                    ) else {"code": type(exc).__name__, "message": str(exc)}
                    execution["errors"].append({"phase": "capture_outputs", **error})
                    failure = failure or f"Output capture failed: {error['message']}"
                try:
                    project.restore()
                    jobs.update_job(job_id, materializationStatus="restored")
                except Exception as exc:
                    error = exc.as_dict() if isinstance(
                        exc, materialization.MaterializationError
                    ) else {"code": type(exc).__name__, "message": str(exc)}
                    execution["errors"].append({"phase": "restore_materialization", **error})
                    jobs.update_job(job_id, materializationStatus="restore_failed")
                    failure = failure or f"Project restoration failed: {error['message']}"
            if report_backups:
                try:
                    _restore_report_backup(report_backups)
                except Exception as exc:
                    error = exc.as_dict() if isinstance(
                        exc, materialization.MaterializationError
                    ) else {"code": type(exc).__name__, "message": str(exc)}
                    execution["errors"].append({"phase": "restore_report", **error})
                    failure = failure or f"Report restoration failed: {error['message']}"

    duration = round(time.time() - start_time, 3)
    execution["finishedAt"] = jobs.now_iso()
    execution["durationSeconds"] = duration
    execution["exitCode"] = result_returncode
    execution["reportBackups"] = report_backups
    _write_json_evidence(job["executionManifestPath"], execution)

    if canceled:
        jobs.update_job(
            job_id,
            status="canceled",
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return
    if failure:
        jobs.update_job(
            job_id,
            status="failed",
            error=failure,
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return
    if result_returncode != 0:
        jobs.update_job(
            job_id,
            status="failed",
            error=f"terminal64.exe exited with code {result_returncode}",
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return
    if not job.get("reportPath") or not os.path.exists(job["reportPath"]):
        jobs.update_job(
            job_id,
            status="failed",
            error="Report not generated",
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
        )
        return

    if job.get("optimizationType"):
        top_passes = job.get("topPasses", DEFAULT_TOP_PASSES)
        cache_details = cache_parser.parse_cache_details(
            job["debugIniPath"], TERMINAL_DIR, top_passes
        )
        optimization_results = list(cache_details.get("rows") or [])
        optimization_cache = cache_details.get("cache")
        if not optimization_results and job.get("optimizationType") != 3:
            optimization_results = optimization_parser.parse_optimization_report(
                job["reportPath"], top_passes
            )
        jobs.update_job(
            job_id,
            status="completed",
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
            optimizationResults=optimization_results,
            optimizationCache=optimization_cache,
        )
        return

    report_html = _read_text_best_effort(job["reportPath"])
    summary = jobs.parse_report_summary(report_html)
    if jobs.is_empty_backtest_summary(summary):
        jobs.update_job(
            job_id,
            status="failed",
            error="Backtest produced empty report (Bars=0, Ticks=0, Symbols=0)",
            exitCode=result_returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
            summary=summary,
        )
        return
    jobs.update_job(
        job_id,
        status="completed",
        exitCode=result_returncode,
        durationSeconds=duration,
        finishedAt=jobs.now_iso(),
        summary=summary,
    )


def _execute_legacy_job(job_id):
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
            log.exception(
                "backtest crashed broker=%s account=%s job=%s",
                BROKER,
                ACCOUNT,
                job_id,
            )
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

    if not os.path.exists(job["reportPath"]) and job.get("optimizationType") == 3:
        # MT5 writes mode-3 optimization output to <base>.symbols.xml.
        symbols_report_path = f"{os.path.splitext(job['reportPath'])[0]}.symbols.xml"
        if os.path.exists(symbols_report_path):
            job["reportPath"] = symbols_report_path
            job["reportName"] = os.path.basename(symbols_report_path)
            jobs.update_job(
                job_id,
                reportPath=symbols_report_path,
                reportName=os.path.basename(symbols_report_path),
            )

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

    if job.get("optimizationType"):
        top_passes = job.get("topPasses", DEFAULT_TOP_PASSES)
        cache_details = cache_parser.parse_cache_details(
            job["debugIniPath"],
            TERMINAL_DIR,
            top_passes,
        )
        optimization_results = list(cache_details.get("rows") or [])
        optimization_cache = cache_details.get("cache")
        result_source = "cache" if optimization_results else "none"
        if not optimization_results and job.get("optimizationType") != 3:
            optimization_results = optimization_parser.parse_optimization_report(
                job["reportPath"],
                top_passes,
            )
            if optimization_results:
                result_source = "xml"
        jobs.update_job(
            job_id,
            status="completed",
            exitCode=result.returncode,
            durationSeconds=duration,
            finishedAt=jobs.now_iso(),
            optimizationResults=optimization_results,
            optimizationCache=optimization_cache,
        )
        log.info(
            "optimization done broker=%s account=%s job=%s duration=%.1fs passes=%s source=%s",
            BROKER,
            ACCOUNT,
            job_id,
            duration,
            len(optimization_results),
            result_source,
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
    log.info(
        "backtest done broker=%s account=%s job=%s duration=%.1fs",
        BROKER,
        ACCOUNT,
        job_id,
        duration,
    )


# ── Status / artifacts ──────────────────────────────────────────────


def get_status(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    return jsonify(jobs.public_payload(job))


def cancel_job(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    if not job.get("projectMode"):
        return jsonify({
            "error": "Cancellation ownership is available only for project-mode jobs",
            "jobId": job_id,
            "status": job.get("status"),
        }), 409
    if job.get("status") in jobs.TERMINAL_STATUSES:
        return jsonify(jobs.public_payload(job))

    requested_action = {"action": "cancel_requested", "at": jobs.now_iso()}
    jobs.update_job(
        job_id,
        cancelRequested=True,
        status="canceling",
        cancelTerminationActions=[requested_action],
    )
    with ACTIVE_PROCESS_LOCK:
        process = ACTIVE_PROCESSES.get(job_id)
    actions = [requested_action]
    if process is not None:
        try:
            actions = _terminate_owned_process(process)
        except Exception as exc:
            error = {"code": type(exc).__name__, "message": str(exc)}
            updated = jobs.update_job(
                job_id,
                cancelRequested=True,
                cancelError=error,
                cancelTerminationActions=actions,
            )
            return jsonify({**jobs.public_payload(updated), "cancelError": error}), 500
    updated = jobs.update_job(
        job_id,
        cancelRequested=True,
        cancelTerminationActions=actions,
    )
    response = jsonify(jobs.public_payload(updated))
    response.status_code = 202
    response.headers["Retry-After"] = "1"
    return response


def _send_job_file(job_id, field, unavailable, mimetype, download_name=None):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    path = job.get(field)
    if not path or not os.path.isfile(path):
        return jsonify({"error": unavailable, "jobId": job_id, "field": field}), 404
    return send_file(
        path,
        mimetype=mimetype,
        as_attachment=False,
        download_name=download_name or os.path.basename(path),
    )


def get_project_manifest(job_id):
    return _send_job_file(
        job_id,
        "projectManifestPath",
        "Project manifest not available",
        "application/json",
    )


def get_project_bundle(job_id):
    return _send_job_file(
        job_id,
        "projectBundlePath",
        "Project bundle not available",
        "application/zip",
    )


def get_submitted_ini(job_id):
    return _send_job_file(
        job_id,
        "submittedIniPath",
        "Submitted INI not available",
        "text/plain",
    )


def get_execution_manifest(job_id):
    return _send_job_file(
        job_id,
        "executionManifestPath",
        "Execution manifest not available yet",
        "application/json",
    )


def get_materialization_audit(job_id):
    return _send_job_file(
        job_id,
        "materializationAuditPath",
        "Materialization audit not available yet",
        "application/json",
    )


def get_output_manifest(job_id):
    return _send_job_file(
        job_id,
        "outputManifestPath",
        "Output manifest not available yet",
        "application/json",
    )


def get_output_artifacts(job_id):
    return _send_job_file(
        job_id,
        "outputArtifactsPath",
        "Output artifacts not available yet",
        "application/zip",
    )


def get_log_delta_manifest(job_id):
    return _send_job_file(
        job_id,
        "logDeltaManifestPath",
        "Log delta manifest not available yet",
        "application/json",
    )


def get_log_delta_artifacts(job_id):
    return _send_job_file(
        job_id,
        "logDeltaArtifactsPath",
        "Log delta artifacts not available yet",
        "application/zip",
    )


def get_report(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    path = job.get("reportPath")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Report not available yet"}), 404
    mimetype = "application/xml" if path.lower().endswith(".xml") else "text/html"
    return send_file(path, mimetype=mimetype, as_attachment=False, download_name=job["reportName"])


def get_log(job_id):
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404
    path = job.get("logPath")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Log not available yet"}), 404
    return send_file(path, mimetype="text/plain", as_attachment=False, download_name=f"{job_id}.log")


def _tail_dir_log(log_dir, lines):
    """Return (path_used, last N non-empty lines) from the newest .log in log_dir."""
    if not os.path.isdir(log_dir):
        return None, ""
    try:
        candidates = sorted(f for f in os.listdir(log_dir) if f.lower().endswith(".log"))
    except OSError:
        return None, ""
    if not candidates:
        return None, ""
    path = os.path.join(log_dir, candidates[-1])
    content = _read_text_best_effort(path)
    tail_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    return path, "\n".join(tail_lines[-lines:])


def _decode_owned_log_bytes(path, raw):
    with open(path, "rb") as handle:
        prefix = handle.read(2)
    if prefix == b"\xff\xfe":
        payload = raw[2:] if raw.startswith(b"\xff\xfe") else raw
        try:
            return payload.decode("utf-16-le"), "utf-16-le", None, None
        except UnicodeDecodeError as exc:
            return None, None, base64.b64encode(raw).decode("ascii"), str(exc)
    if prefix == b"\xfe\xff":
        payload = raw[2:] if raw.startswith(b"\xfe\xff") else raw
        try:
            return payload.decode("utf-16-be"), "utf-16-be", None, None
        except UnicodeDecodeError as exc:
            return None, None, base64.b64encode(raw).decode("ascii"), str(exc)
    try:
        return raw.decode("utf-8"), "utf-8", None, None
    except UnicodeDecodeError as exc:
        return None, None, base64.b64encode(raw).decode("ascii"), str(exc)


def _owned_project_log_tail(job, n_lines):
    records = []
    if job.get("logDeltaManifestPath") and os.path.isfile(job["logDeltaManifestPath"]):
        with open(job["logDeltaManifestPath"], "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        candidates = [
            {
                "key": record["key"],
                "classification": record["classification"],
                "path": record.get("deltaPath"),
                "rawSize": record.get("deltaSize"),
                "rawSha256": record.get("deltaSha256"),
            }
            for record in stored.get("files", [])
            if record.get("deltaPath") and os.path.isfile(record["deltaPath"])
        ]
        source = "captured_delta"
    elif job.get("logBaselinePath") and os.path.isfile(job["logBaselinePath"]):
        with open(job["logBaselinePath"], "r", encoding="utf-8") as handle:
            baseline_payload = json.load(handle)
        baseline = baseline_payload.get("files") or {}
        current = _log_inventory()
        candidates = []
        for key, after in current.items():
            before = baseline.get(key)
            offset = 0
            classification = "created"
            if before is not None:
                if after["size"] >= before["size"]:
                    prefix_sha256, prefix_size = _hash_prefix(after["path"], before["size"])
                    if prefix_size == before["size"] and prefix_sha256 == before["sha256"]:
                        offset = before["size"]
                        classification = "appended"
                    else:
                        classification = "replaced"
                else:
                    classification = "truncated_or_replaced"
            if after["size"] == offset:
                continue
            candidates.append({
                "key": key,
                "classification": classification,
                "path": after["path"],
                "offset": offset,
                "rawSize": after["size"] - offset,
                "rawSha256": None,
            })
        source = "live_from_owned_baseline"
    else:
        return {
            "source": "owned_baseline_unavailable",
            "baselineAvailable": False,
            "records": [],
            "terminalLog": "",
            "testerLog": "",
        }

    grouped = {"terminal": [], "tester": []}
    for candidate in candidates:
        path = candidate["path"]
        offset = candidate.get("offset", 0)
        with open(path, "rb") as handle:
            handle.seek(offset)
            raw = handle.read()
        raw_sha256 = hashlib.sha256(raw).hexdigest()
        text, encoding, raw_base64, decode_error = _decode_owned_log_bytes(path, raw)
        tail_text = ""
        if text is not None:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            tail_text = "\n".join(lines[-n_lines:])
        scope = candidate["key"].split("/", 1)[0]
        if tail_text and scope in grouped:
            grouped[scope].append(tail_text)
        records.append({
            "key": candidate["key"],
            "classification": candidate["classification"],
            "rawSize": len(raw),
            "rawSha256": raw_sha256,
            "encoding": encoding,
            "text": tail_text if text is not None else None,
            "rawBase64": raw_base64,
            "decodeError": decode_error,
        })
    return {
        "source": source,
        "baselineAvailable": True,
        "records": records,
        "terminalLog": "\n".join(grouped["terminal"]),
        "testerLog": "\n".join(grouped["tester"]),
    }


def get_tail(job_id):
    """Live log tail — works for queued/running/completed jobs.

    Returns JSON with:
      - terminalLog: last N lines of the MT5 terminal journal
      - testerLog:   last N lines of the Strategy Tester sub-log (if present)
      - runLog:      stdout/stderr captured from terminal64.exe (usually sparse)
    """
    try:
        n_lines = int(request.args.get("lines", 200))
        n_lines = max(10, min(n_lines, 1000))
    except (TypeError, ValueError):
        n_lines = 200

    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}"}), 404

    # run.log — stdout/stderr of terminal64.exe (sparse but useful on errors)
    run_log = ""
    log_path = job.get("logPath")
    if log_path:
        content = _read_text_best_effort(log_path)
        run_tail = [ln.strip() for ln in content.splitlines() if ln.strip()]
        run_log = "\n".join(run_tail[-50:])

    if job.get("projectMode"):
        owned = _owned_project_log_tail(job, n_lines)
        return jsonify({
            "jobId": job_id,
            "externalRunId": job.get("externalRunId"),
            "projectId": job.get("projectId"),
            "status": job.get("status"),
            "startedAt": job.get("startedAt"),
            "finishedAt": job.get("finishedAt"),
            "error": job.get("error"),
            "runLog": run_log,
            "terminalLog": owned["terminalLog"],
            "testerLog": owned["testerLog"],
            "logEvidenceSource": owned["source"],
            "logBaselineAvailable": owned["baselineAvailable"],
            "logEvidence": owned["records"],
        })

    # MT5 terminal journal: <TERMINAL_DIR>/logs/YYYYMMDD.log
    terminal_log_dir = os.path.join(TERMINAL_DIR, "logs")
    terminal_log_file, terminal_log = _tail_dir_log(terminal_log_dir, n_lines)

    # Strategy Tester sub-log: <TERMINAL_DIR>/Tester/logs/YYYYMMDD.log
    tester_log_dir = os.path.join(TERMINAL_DIR, "Tester", "logs")
    tester_log_file, tester_log = _tail_dir_log(tester_log_dir, n_lines)

    return jsonify({
        "jobId": job_id,
        "status": job.get("status"),
        "startedAt": job.get("startedAt"),
        "finishedAt": job.get("finishedAt"),
        "error": job.get("error"),
        "runLog": run_log,
        "terminalLog": terminal_log,
        "testerLog": tester_log,
        "terminalLogFile": os.path.basename(terminal_log_file) if terminal_log_file else None,
        "testerLogFile": os.path.basename(tester_log_file) if tester_log_file else None,
    })
