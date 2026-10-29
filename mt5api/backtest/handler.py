"""Backtest Flask views: build-ini, run, status, report, log, cancel.

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

Cancel flow: POST /backtest/<jobId>/cancel marks the job cancelled and flags
it; the worker checks the flag wherever it waits and stops there. A run that
holds RUN_LOCK is this terminal directory's only one, so its tester processes
are killed; a job still waiting is never allowed to touch the run in front.
"""
from __future__ import annotations

import configparser
import contextlib
import io
import os
import shutil
import subprocess
import threading
import time
import uuid

import psutil

from flask import Response, jsonify, request, send_file

from mt5api import symbol_cache, terminal_mcp
from mt5api.backtest import cache_parser, ini_builder, jobs, optimization_parser, set_builder
from mt5api.config import (
    ACCOUNT,
    BROKER,
    DISABLE_TERMINAL_MCP,
    INSTANCE,
    LOG_DIR,
    TERMINAL_DIR,
    TERMINAL_PATH,
    SYMBOL_SUFFIX,
    SYMBOL_SUFFIX_CONFIGURED,
    ASSETS_DIR,
    BACKTEST_MAX_TIMEOUT_SECONDS,
    BACKTEST_TIMEOUT_SECONDS,
    BACKTEST_JOB_DIR,
    load_yaml_config,
    parse_duration_to_seconds,
)
from mt5api.jsonkeys import accept_snake_keys, with_legacy_keys
from mt5api.logger import log

RUN_LOCK = threading.Lock()

# Jobs a cancel was asked for, and jobs whose worker thread still runs, in this
# process. Both are guarded by _CANCEL_LOCK.
_CANCELLED: set = set()
_ACTIVE_WORKERS: set = set()
_CANCEL_LOCK = threading.Lock()
# The job holding RUN_LOCK, if any. Only its tester processes may be killed by
# a cancel: holding the lock is what makes this terminal directory's processes
# that job's own.
_LOCK_HOLDER = {"job_id": None}
# How often a worker waiting for RUN_LOCK looks at its cancel flag.
RUN_LOCK_POLL_SECONDS = 1
# How long a cancelled run waits for its tester processes, agents included, to
# exit before it lets the next job have the terminal. A metatester64 agent can
# outlive terminal64.exe by seconds, and a launch while it still holds the
# agent ports fails with "bind error [10048]".
CANCEL_SETTLE_SECONDS = 60
# How long POST /cancel waits for the worker to stop before answering 202.
# Under nginx's default 60 s proxy timeout, with room for the kill's own grace.
CANCEL_WAIT_SECONDS = 40
# Terminate-to-kill grace for each kill the cancel route sends, and the spacing
# between them while the worker has not stopped yet.
CANCEL_KILL_GRACE_SECONDS = 5
CANCEL_POLL_SECONDS = 0.5
CANCEL_ERROR = "Cancelled by request"
WORKER_CRASH_ERROR = "Backtest worker crashed before the run finished"
WORKER_START_ERROR = "Backtest worker could not be started"

# How long to keep looking for a replacement terminal process after the one we
# launched exits 0 without a report. Covers the ~7s LiveUpdate gap with margin.
RELAUNCH_GRACE_SECONDS = 30
# Only a process that died fast enough to be an update restart is worth waiting
# on; a genuine failure after a real run must still fail immediately.
RELAUNCH_MAX_EXIT_SECONDS = 60
# The report is written just before the terminal exits.
REPORT_SETTLE_SECONDS = 2
DIAGNOSTIC_TAIL_CHARS = 4000
DEFAULT_TOP_PASSES = 50
MAX_TOP_PASSES = 500


# ── INI builder route ───────────────────────────────────────────────


def build_ini_route():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    body = request.get_json(silent=True) or {}
    try:
        if isinstance(body, dict):
            body = accept_snake_keys(body, ini_builder.CAMEL_CASE_FIELDS)
        ini_text = ini_builder.build_ini(body)
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


def _form_field(name: str, legacy_name: str) -> str | None:
    """A form field under its snake_case name or its deprecated camelCase
    one. Both with different values is a ValueError."""
    value = request.form.get(name)
    legacy = request.form.get(legacy_name)
    if value is not None and legacy is not None and value != legacy:
        raise ValueError(f"{name} and {legacy_name} are the same field; send one")
    return value if value is not None else legacy


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
    # RawConfigParser, not ConfigParser: MT5 INI values are literals and a bare
    # '%' is ordinary text in them (EA comments, percentage inputs). Interpolation
    # rejects those outright — ConfigParser raises InterpolationSyntaxError on
    # "Risk 2% per trade" — which fails the submission before the test ever runs.
    parser = configparser.RawConfigParser()
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

    # Few brokers suffix their whole book. Eightcap Global suffixes 56 FX pairs
    # (EURUSD.i) and leaves 785 metals/indices/crypto bare (XAUUSD, ASX200), so
    # appending unconditionally asks the tester for a symbol that cannot exist
    # and the run comes back empty. Only skip the remap on positive evidence:
    # a cached symbol list that HAS the bare name and LACKS the suffixed one.
    # Absent cache => append, exactly as this did before the cache existed.
    known = symbol_cache.load(TERMINAL_DIR)
    if known is not None and remapped not in known and symbol in known:
        log.info(
            "backtest symbol kept unsuffixed broker=%s account=%s %s "
            "(broker has no %s)",
            BROKER,
            ACCOUNT,
            symbol,
            remapped,
        )
        return

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


#: How much of a log file a tail is allowed to touch. A terminal writing a
#: multi-year backtest grows its Tester log to gigabytes WHILE the run is
#: polled, so tailing must stay O(tail), never O(file) — a whole-file read of
#: one of those seizes the process for a minute per call (whole-file bytes +
#: a decoded str copy + a splitlines() list, all while holding the GIL), which
#: starves every other request including /ping and the container healthcheck.
TAIL_MAX_BYTES = 256 * 1024


def _read_tail_text(path, max_bytes=TAIL_MAX_BYTES):
    """Decode at most the final ``max_bytes`` of ``path``.

    Encoding is sniffed from the file's first two bytes: a BOM means
    UTF-16-LE (how MT5 writes its logs), and so does a NUL second byte —
    UTF-16-LE of any ASCII-leading text, which covers BOM-less UTF-16 logs.
    Anything else decodes as UTF-8 (run.log). The read then seeks to the
    final window, aligned to a 2-byte boundary so UTF-16 code units stay
    intact. When the window starts mid-file, everything up to the first
    newline is dropped — a truncated first line reads as garbage, and a tail
    endpoint never needs it.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(2)
            utf16 = head == b"\xff\xfe" or (len(head) == 2 and head[1] == 0)
            size = handle.seek(0, os.SEEK_END)
            offset = max(0, size - max_bytes)
            if utf16 and offset % 2:
                offset -= 1
            handle.seek(offset)
            raw = handle.read()
    except OSError:
        return ""

    if utf16:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    if offset > 0:
        first_break = text.find("\n")
        if first_break != -1:
            text = text[first_break + 1:]
    return text


def _tail_terminal_log(lines=20):
    """Tail of the terminal's most recently written run log.

    Picked by modification time, not by name. The logs are named `<date>.log`,
    but the directory also holds `metaeditor.log`, which sorts after every one
    of them ("m" > "2") and never changes — so an alphabetical pick attached a
    months-old compile tail to every failure message and hid the actual reason
    the run died.
    """
    log_dir = os.path.join(TERMINAL_DIR, "logs")
    if not os.path.isdir(log_dir):
        return ""

    try:
        candidates = [
            entry
            for entry in os.scandir(log_dir)
            if entry.is_file()
            and entry.name.endswith(".log")
            and entry.name.lower() != "metaeditor.log"
        ]
    except OSError:
        return ""

    if not candidates:
        return ""

    try:
        newest = max(candidates, key=lambda entry: entry.stat().st_mtime)
    except OSError:
        return ""

    content = _read_tail_text(newest.path)

    tail_lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not tail_lines:
        return ""
    return "\n".join(tail_lines[-lines:])


#: The tester runs as terminal64.exe plus one metatester64.exe per agent. The
#: agents are what hold the localhost ports a later run needs, so a cleanup that
#: only accounts for terminal64.exe leaves the terminal unusable.
TESTER_PROCESS_NAMES = frozenset({"terminal64.exe", "metatester64.exe"})


def _normalize_path(path):
    return path.replace("\\", "/").lower().rstrip("/")


#: Resolved spellings per terminal dir. Only a link that actually resolved is
#: stored: if the share is not reachable yet, realpath() returns the configured
#: path unchanged, and caching that would hide the resolved form until restart.
_resolved_terminal_dir_forms = {}


def _terminal_dir_forms(terminal_dir):
    """Every spelling of ``terminal_dir`` a process's exe path can come back as.

    In the VM, ``Desktop\\Shared`` is a link to ``\\\\host.lan\\Data`` and Windows
    reports a process's image by its resolved path, so a terminal launched as
    ``C:\\Users\\Docker\\Desktop\\Shared\\terminals\\...`` shows up as
    ``\\\\host.lan\\Data\\terminals\\...``. Matching only the configured spelling
    found nothing: the self-relaunch wait never fired and the timeout and
    startup cleanups killed nothing.
    """
    cached = _resolved_terminal_dir_forms.get(terminal_dir)
    if cached is not None:
        return cached

    configured = _normalize_path(terminal_dir)
    try:
        resolved = _normalize_path(os.path.realpath(terminal_dir))
    except (OSError, ValueError) as exc:
        log.warning(
            "terminal dir resolve failed, matching the configured path only dir=%s err=%s",
            terminal_dir,
            exc,
        )
        return frozenset({configured})

    forms = frozenset({configured, resolved})
    if resolved != configured:
        _resolved_terminal_dir_forms[terminal_dir] = forms
    return forms


def _in_terminal_dir(exe):
    """True when ``exe`` lives inside THIS terminal directory.

    Compared by normalized path components, not by substring: a sibling at
    ``...\\a2\\terminal64.exe`` must never match a terminal at ``...\\a``.
    """
    if not exe:
        return False
    path = _normalize_path(exe)
    return any(
        path == base or path.startswith(base + "/")
        for base in _terminal_dir_forms(TERMINAL_DIR)
    )


def _terminal_processes(names=TESTER_PROCESS_NAMES):
    """Processes of the given names running from THIS terminal directory.

    Matched by directory rather than by the PID we spawned on purpose: MT5 may
    replace the process we launched (see _await_self_relaunch), and the agents
    are never ours to begin with. Sibling terminals live in sibling directories,
    so this never reaches across to another instance's processes.
    """
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info.get("name") or "").lower() not in names:
                continue
            exe = proc.info.get("exe") or ""
            if _in_terminal_dir(exe):
                yield proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


#: How long a fresh launch waits for a leftover LiveUpdate updater to finish
#: before stopping it. An updater still alive when the next job starts is
#: almost always stuck: the relaunch holds the file it needs to replace.
UPDATER_WAIT_SECONDS = 120


def _updater_processes():
    """MT5 LiveUpdate updaters working on THIS terminal directory.

    When a broker's server ships a new build, a launch of a terminal with the
    update pending starts `<AppData>\\MetaQuotes\\Terminal\\<hash>\\liveupdate\\
    terminal64.exe /update /path:"<terminal dir>"` and exits. If the next job
    relaunches the terminal before the updater has replaced terminal64.exe, the
    updater can never finish and never exits, and each further launch starts
    another one. They live outside the terminal folder, so _terminal_processes
    never saw them. Each holds ~48 GDI and ~36 USER objects; about 1,000 per VM
    used up the Windows session (65,536 GDI objects) and every terminal launch
    then failed with exit 10053 or 10012 ("not enough handles to start the
    platform").
    """
    forms = set(_terminal_dir_forms(TERMINAL_DIR))
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() != "terminal64.exe":
                continue
            if "/liveupdate/" not in _normalize_path(proc.info.get("exe") or ""):
                continue
            for arg in proc.info.get("cmdline") or []:
                if arg.lower().startswith("/path:"):
                    if _normalize_path(arg[len("/path:"):].strip('"')) in forms:
                        yield proc
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _clear_stale_updaters(wait_seconds=UPDATER_WAIT_SECONDS):
    """Before a launch: give a leftover updater for this terminal time to
    finish, then stop it. Returns how many were stopped."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not any(True for _ in _updater_processes()):
            return 0
        time.sleep(5)
    stale = list(_updater_processes())
    for proc in stale:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if stale:
        log.warning(
            "backtest: stopped %d stuck LiveUpdate updater(s) broker=%s account=%s",
            len(stale), BROKER, ACCOUNT,
        )
    return len(stale)


def kill_terminal_processes(grace_seconds=10):
    """Stop this terminal's tester processes and its LiveUpdate updaters.
    Returns how many were signalled.

    Terminate first, then kill whatever is still standing, so MT5 gets the
    chance to release its files cleanly before being shot.
    """
    victims = list(_terminal_processes()) + list(_updater_processes())
    if not victims:
        return 0
    for proc in victims:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(victims, timeout=grace_seconds)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if alive:
        psutil.wait_procs(alive, timeout=grace_seconds)
    return len(victims)


def _terminal_process_alive():
    """True while a terminal64.exe belonging to THIS terminal directory runs.

    Only terminal64.exe: this answers "is a run still going", and the agents
    come and go within one.
    """
    for _ in _terminal_processes({"terminal64.exe"}):
        return True
    return False


def _report_candidates(job):
    """Every path that counts as this run's output.

    Mode-3 optimizations write `<base>.symbols.xml` instead of the .htm the INI
    asks for, so a mode-3 run has two acceptable outputs and checking only the
    .htm would make a finished job look empty.
    """
    paths = [job["reportPath"]]
    if job.get("optimizationType") == 3:
        paths.append(f"{os.path.splitext(job['reportPath'])[0]}.symbols.xml")
    return paths


def _any_report_exists(paths):
    return any(os.path.exists(path) for path in paths)


def _await_self_relaunch(job_id, report_paths, deadline):
    """Wait out an MT5 self-relaunch, returning True if the report then lands.

    When a LiveUpdate is pending, MT5 applies it by having the process we
    launched spawn the updater and exit 0 within a few seconds, then relaunches
    itself to run the actual test. `subprocess.run` returns for that first,
    short-lived process, so the report legitimately does not exist yet and the
    job used to be failed as "Report not generated" while the backtest it was
    reporting on went on to finish perfectly well minutes later.

    Seen across the build 6090 rollout: terminal exits at T+3s, the replacement
    starts at T+9s, and the test finishes at T+5m34s having written a valid
    report. One sacrificial job per terminal, on every MT5 build push.
    """
    # The updater runs from AppData, not from TERMINAL_DIR, so there is a gap
    # with no matching process at all between the exit and the relaunch —
    # ~7s when observed. Poll the whole grace window before concluding that
    # nothing is coming back.
    grace_end = min(time.time() + RELAUNCH_GRACE_SECONDS, deadline)
    while time.time() < grace_end:
        if _cancel_requested(job_id):
            return False
        if _terminal_process_alive():
            break
        if _any_report_exists(report_paths):
            return True
        time.sleep(1)
    else:
        return _any_report_exists(report_paths)

    log.info(
        "backtest terminal relaunched itself (pending LiveUpdate) broker=%s "
        "account=%s job=%s — waiting for the replacement run",
        BROKER, ACCOUNT, job_id,
    )
    while time.time() < deadline:
        if _cancel_requested(job_id):
            return False
        if not _terminal_process_alive():
            break
        time.sleep(2)

    # MT5 writes the report and then exits, so the file can still be settling
    # in the moment the process disappears.
    time.sleep(REPORT_SETTLE_SECONDS)
    return _any_report_exists(report_paths)


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
        top_passes = _parse_top_passes(_form_field("top_passes", "topPasses"))
        timeout_seconds = (
            parse_duration_to_seconds(timeout_value)
            if timeout_value
            else BACKTEST_TIMEOUT_SECONDS
        )
        if timeout_seconds <= 0 or timeout_seconds > BACKTEST_MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"timeout must be > 0 and <= {BACKTEST_MAX_TIMEOUT_SECONDS}s "
                f"(got {timeout_seconds}s)"
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
        optimization_type = _optimization_type(parser)
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
        # Sibling terminals share broker+account and differ only by instance, so
        # without this the startup sweep cannot tell whose job it is looking at.
        "instance": INSTANCE,
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
        "optimizationType": optimization_type,
        "optimizationResults": None,
        "optimizationCache": None,
        "topPasses": top_passes,
        "timeoutSeconds": timeout_seconds,
    }
    jobs.store_job(job)

    with _CANCEL_LOCK:
        _ACTIVE_WORKERS.add(job_id)
    try:
        threading.Thread(target=_execute_job, args=(job_id,), daemon=True).start()
    except Exception:  # noqa: BLE001 - a job without a worker must not stay queued
        log.exception(
            "backtest worker start failed broker=%s account=%s job=%s", BROKER, ACCOUNT, job_id
        )
        with _CANCEL_LOCK:
            _ACTIVE_WORKERS.discard(job_id)
        jobs.update_job(
            job_id, status="failed", error=WORKER_START_ERROR, finishedAt=jobs.now_iso()
        )
        return jsonify({"error": WORKER_START_ERROR, "code": "WORKER_START_FAILED"}), 503

    response = jsonify(jobs.public_payload(job))
    response.status_code = 202
    response.headers["Retry-After"] = str(jobs.POLL_AFTER_SECONDS)
    return response


# ── Worker ──────────────────────────────────────────────────────────


class JobCancelled(Exception):
    """The job was cancelled; raised where the worker notices, to stop it there."""


def _cancel_requested(job_id):
    with _CANCEL_LOCK:
        return job_id in _CANCELLED


def _raise_if_cancelled(job_id):
    if _cancel_requested(job_id):
        raise JobCancelled(job_id)


def _worker_active(job_id):
    with _CANCEL_LOCK:
        return job_id in _ACTIVE_WORKERS


def active_worker_ids():
    """Jobs whose worker thread still runs, whatever their status says. A
    cancelled job's worker can spend up to CANCEL_SETTLE_SECONDS (plus
    RELAUNCH_GRACE_SECONDS after the lock) killing its tester processes."""
    with _CANCEL_LOCK:
        return frozenset(_ACTIVE_WORKERS)


@contextlib.contextmanager
def _run_lock(job_id):
    """Hold RUN_LOCK for `job_id`, giving up the wait if the job is cancelled."""
    while not RUN_LOCK.acquire(timeout=RUN_LOCK_POLL_SECONDS):
        _raise_if_cancelled(job_id)
    _LOCK_HOLDER["job_id"] = job_id
    try:
        yield
    finally:
        _LOCK_HOLDER["job_id"] = None
        RUN_LOCK.release()


def _settle_after_cancel(job_id, watch_seconds=0):
    """Inside RUN_LOCK: stop the cancelled run and wait for its processes to go,
    so the next job does not launch onto agents still holding their ports.

    `watch_seconds` keeps watching that long and kills whatever appears: after
    the launched process exits, MT5 can start its replacement (the run itself,
    on a LiveUpdate hand-off) seconds later.
    """
    killed = kill_terminal_processes()
    quiet_from = time.time() + watch_seconds
    deadline = quiet_from + CANCEL_SETTLE_SECONDS
    while True:
        alive = any(True for _ in _terminal_processes())
        now = time.time()
        if not alive and now >= quiet_from:
            break
        if now >= deadline:
            killed += kill_terminal_processes()
            break
        if alive and watch_seconds:
            killed += kill_terminal_processes()
        time.sleep(1)
    log.info(
        "backtest cancelled broker=%s account=%s job=%s killed=%d",
        BROKER, ACCOUNT, job_id, killed,
    )


def _stop_after_lock_released(job_id):
    """A cancel noticed after the worker left RUN_LOCK (the self-relaunch wait,
    the report checks): take the lock back so no other job launches meanwhile,
    then stop the run and watch for a replacement terminal. If another job
    already holds the lock, its processes are its own and nothing is killed."""
    if not RUN_LOCK.acquire(timeout=CANCEL_SETTLE_SECONDS):
        log.warning(
            "backtest cancel: another job holds the terminal broker=%s account=%s job=%s",
            BROKER, ACCOUNT, job_id,
        )
        return
    _LOCK_HOLDER["job_id"] = job_id
    try:
        _settle_after_cancel(job_id, watch_seconds=RELAUNCH_GRACE_SECONDS)
    finally:
        _LOCK_HOLDER["job_id"] = None
        RUN_LOCK.release()


def _fail_if_active(job_id, error):
    job = jobs.load_job(job_id)
    if job is None or job.get("status") not in jobs.ACTIVE_STATUSES:
        return
    jobs.update_job(job_id, status="failed", error=error, finishedAt=jobs.now_iso())


def _execute_job(job_id):
    """Run one job and, whatever happens, never leave it queued or running.

    The body used to run unguarded up to its first `try`: a WinError 5 from the
    "running" state write on the VM share killed the thread there, and the job
    answered "running" to every poll with no terminal ever started.
    """
    with _CANCEL_LOCK:
        _ACTIVE_WORKERS.add(job_id)
    try:
        _run_job(job_id)
    except JobCancelled:
        log.info(
            "backtest cancelled before launch broker=%s account=%s job=%s",
            BROKER, ACCOUNT, job_id,
        )
    except BaseException as exc:  # noqa: BLE001 - a dead worker must not leave its job active
        log.exception(
            "backtest worker crashed broker=%s account=%s job=%s", BROKER, ACCOUNT, job_id
        )
        _fail_if_active(job_id, f"{WORKER_CRASH_ERROR}: {exc}")
        if not isinstance(exc, Exception):
            raise
    finally:
        with _CANCEL_LOCK:
            _ACTIVE_WORKERS.discard(job_id)
            _CANCELLED.discard(job_id)


def _run_job(job_id):
    job = jobs.load_job(job_id)
    if job is None or _cancel_requested(job_id):
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
    with _run_lock(job_id):
        _raise_if_cancelled(job_id)
        jobs.update_job(job_id, status="running", startedAt=started_at)
        try:
            shutil.copyfile(job["stagedExpertPath"], expert_dest)
            if set_dest:
                shutil.copyfile(job["stagedSetPath"], set_dest)
            parser = _parse_ini(_read_text_best_effort(job["debugIniPath"]))
            ini_path = os.path.join(job["stageDir"], "tester.ini")
            _write_utf16_ini(parser, ini_path)
            # MT5 rewrites assistant.ini on exit, so this runs before every launch.
            terminal_mcp.apply(DISABLE_TERMINAL_MCP, TERMINAL_DIR, "backtest")

            cmd = [TERMINAL_PATH, "/portable", f"/config:{ini_path}"]
            log.info(
                "backtest start broker=%s account=%s job=%s report=%s",
                BROKER, ACCOUNT, job_id, job["reportName"],
            )
            # A launch on top of a stuck updater starts another updater that
            # cannot finish either (see _updater_processes).
            _clear_stale_updaters()
            _raise_if_cancelled(job_id)
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
                    # The cancel route killed the terminal, which is why it returned.
                    _raise_if_cancelled(job_id)
                except subprocess.TimeoutExpired:
                    duration = round(time.time() - start_time, 3)
                    # subprocess.run kills the process it started, but not the
                    # metatester64.exe agents it spawned, and not a terminal MT5
                    # relaunched in place of ours. Those keep the terminal's
                    # localhost agent ports bound, so every later run on this
                    # terminal dies with "bind error [10048]" until the host is
                    # rebooted. Clear the whole directory's tester processes.
                    killed = kill_terminal_processes()
                    error = f"Backtest timed out after {job['timeoutSeconds']}s"
                    if killed:
                        error = f"{error} (killed {killed} leftover tester process(es))"
                    log.warning(
                        "backtest timed out broker=%s account=%s job=%s killed=%d",
                        BROKER, ACCOUNT, job_id, killed,
                    )
                    jobs.update_job(
                        job_id,
                        status="failed",
                        error=error,
                        durationSeconds=duration,
                        finishedAt=jobs.now_iso(),
                    )
                    return
        except JobCancelled:
            _settle_after_cancel(job_id)
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

    if _cancel_requested(job_id):
        # Cancelled between the terminal's exit and here: nothing left to judge.
        _stop_after_lock_released(job_id)
        return

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

    # A clean exit with no report, fast enough to be an update restart rather
    # than a real run, means MT5 probably relaunched itself — wait for the
    # replacement rather than failing a backtest that is still going to finish.
    report_paths = _report_candidates(job)
    if duration < RELAUNCH_MAX_EXIT_SECONDS and not _any_report_exists(report_paths):
        _await_self_relaunch(job_id, report_paths, start_time + job["timeoutSeconds"])
        duration = round(time.time() - start_time, 3)
        if _cancel_requested(job_id):
            # Cancelled while waiting for MT5's replacement terminal: that one is the run.
            _stop_after_lock_released(job_id)
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


def cancel_backtest(job_id):
    """POST /backtest/<jobId>/cancel: stop a job and mark it cancelled.

    Idempotent. A job already finished is answered as it is. A waiting job is
    dropped before it launches. A running job that holds RUN_LOCK has this
    terminal's tester processes killed, and the answer waits (up to
    CANCEL_WAIT_SECONDS) until its worker has let go of the terminal: 200 when
    it has, 202 while it is still stopping. A job whose worker is gone is only
    marked: no other run's processes are touched.
    """
    job = jobs.load_job(job_id)
    if job is None:
        return jsonify({"error": f"Backtest job not found: {job_id}", "code": "JOB_NOT_FOUND"}), 404
    if not jobs.owns_job(job):
        return jsonify({
            "error": f"Backtest job {job_id} belongs to another terminal",
            "code": "JOB_NOT_OWNED",
        }), 409
    if job.get("status") in jobs.TERMINAL_STATUSES:
        return jsonify(jobs.public_payload(job))

    with _CANCEL_LOCK:
        _CANCELLED.add(job_id)
    if jobs.cancel_if_active(job_id, error=CANCEL_ERROR, finishedAt=jobs.now_iso()) is None:
        # It finished between the check above and now: answer it as it is.
        with _CANCEL_LOCK:
            _CANCELLED.discard(job_id)
        return jsonify(jobs.public_payload(jobs.load_job(job_id)))
    log.info(
        "backtest cancel requested broker=%s account=%s job=%s was=%s",
        BROKER, ACCOUNT, job_id, job.get("status"),
    )

    killed = 0
    next_kill_at = 0.0
    deadline = time.time() + CANCEL_WAIT_SECONDS
    while _worker_active(job_id) and time.time() < deadline:
        if _LOCK_HOLDER["job_id"] == job_id and time.time() >= next_kill_at:
            killed += kill_terminal_processes(grace_seconds=CANCEL_KILL_GRACE_SECONDS)
            next_kill_at = time.time() + CANCEL_KILL_GRACE_SECONDS
        time.sleep(CANCEL_POLL_SECONDS)

    stopped = not _worker_active(job_id)
    if stopped:
        # A worker that was already gone never clears its own flag.
        with _CANCEL_LOCK:
            _CANCELLED.discard(job_id)
    payload = jobs.public_payload(jobs.load_job(job_id))
    payload["killed"] = killed
    payload["stopped"] = stopped
    return jsonify(payload), (200 if stopped else 202)


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
    return send_file(
        path,
        mimetype="text/plain",
        as_attachment=False,
        download_name=f"{job_id}.log",
    )


def _tail_dir_log(log_dir, lines):
    """Return (path_used, last N non-empty lines) from the newest .log in log_dir.

    Newest by MODIFICATION TIME, excluding `metaeditor.log` — the same two
    rules `_tail_terminal_log` already applies, and for the same reason: the
    logs are `<date>.log` files plus a `metaeditor.log` that sorts after all
    of them ("m" > "2") and never changes, so an alphabetical pick returned a
    stale compile log instead of the run being polled.

    Bounded read (`_read_tail_text`): this runs on the live /tail endpoint,
    which the backend polls once a minute for every running job, against a
    Tester log that reaches gigabytes mid-run. The prior whole-file read took
    45-65 s per call on such a log, starving every thread in the process —
    /ping and the container healthcheck included — which made a healthy
    terminal look wedged from the outside.
    """
    if not os.path.isdir(log_dir):
        return None, ""
    try:
        candidates = [
            entry
            for entry in os.scandir(log_dir)
            if entry.is_file()
            and entry.name.lower().endswith(".log")
            and entry.name.lower() != "metaeditor.log"
        ]
    except OSError:
        return None, ""
    if not candidates:
        return None, ""
    try:
        newest = max(candidates, key=lambda entry: entry.stat().st_mtime)
    except OSError:
        return None, ""
    content = _read_tail_text(newest.path)
    tail_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    return newest.path, "\n".join(tail_lines[-lines:])


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

    # run.log — stdout/stderr of terminal64.exe. Usually sparse, but "usually"
    # is not a bound: a chatty terminal can grow it without limit, and this
    # endpoint is polled — same O(tail) rule as _tail_dir_log.
    run_log = ""
    log_path = job.get("logPath")
    if log_path:
        content = _read_tail_text(log_path)
        run_tail = [ln.strip() for ln in content.splitlines() if ln.strip()]
        run_log = "\n".join(run_tail[-50:])

    # MT5 terminal journal: <TERMINAL_DIR>/logs/YYYYMMDD.log
    terminal_log_dir = os.path.join(TERMINAL_DIR, "logs")
    terminal_log_file, terminal_log = _tail_dir_log(terminal_log_dir, n_lines)

    # Strategy Tester sub-log: <TERMINAL_DIR>/Tester/logs/YYYYMMDD.log
    tester_log_dir = os.path.join(TERMINAL_DIR, "Tester", "logs")
    tester_log_file, tester_log = _tail_dir_log(tester_log_dir, n_lines)

    return jsonify(with_legacy_keys({
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
    }))
