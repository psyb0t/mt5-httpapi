"""Apply the WebRequest allowlist by driving MT5's Options dialog with AutoIt.

Why not just write the file? On the dockur-VM terminal build the allowlist is
NOT stored in (or read back from) ``common.ini`` — it lives in the machine-bound
``MQL5\\experts.dat`` and does not survive a terminal restart even when set in
MT5's own Options dialog. So file injection can't provision it there. Instead we
set it the way a user would: open Tools -> Options -> Expert Advisors and add the
URLs. WebRequest then works immediately in-session (verified with a probe EA:
``ret=200`` on an allowed URL). Because MT5 drops the list on restart, this is a
re-appliable operation (a dedicated call, plus an optional boot re-apply).

The heavy lifting is a portable AutoIt interpreter + script shipped under
``assets/autoit/`` (live-mounted into the VM). MT5 runs elevated (``-Verb RunAs``)
and Windows UIPI blocks a non-elevated process from sending it input — but the API
process itself already runs elevated here (verified: AutoIt reports ``IsAdmin=1``),
so a plain, blocking ``subprocess.run`` inherits that elevation, drives MT5 fine,
and lets us capture the exit code. A ``use_runas`` path (async ``Start-Process
-Verb RunAs`` + log polling) is kept as a fallback for non-elevated deployments.
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time

from mt5api.config import ACCOUNT, ASSETS_DIR, BROKER, INI_FILE, INSTANCE, TERMINAL_DIR
from mt5api.logger import log

AUTOIT_DIR = os.path.join(ASSETS_DIR, "autoit")
AUTOIT_EXE = os.path.join(AUTOIT_DIR, "AutoIt3_x64.exe")

_LOG_NAME = "webrequest_autoit.log"
_URLS_NAME = "webrequest_apply_urls.txt"

# Machine-wide mutex serializing GUI automation across every terminal's API
# process on this host (see _gui_lock).
_GUI_MUTEX_NAME = "Global\\mt5_httpapi_webrequest_autoit"
_GUI_LOCK_WAIT_MS = 180_000  # 3 min — comfortably longer than any real apply


@contextlib.contextmanager
def _gui_lock(wait_ms: int = _GUI_LOCK_WAIT_MS):
    """Serialize GUI automation across all terminal API processes on this host.

    Desktop input (window focus + the keyboard) is a single shared resource, so
    two AutoIt applies running at once would steal focus from each other and leak
    keystrokes into the wrong terminal — even producing a RESULT=OK that typed a
    URL into the wrong window. Each terminal runs its own API *process*, so an
    in-process ``threading.Lock`` is not enough; we take a named Windows kernel
    mutex, which is machine-wide. It is crash-safe: if a holder dies, the kernel
    hands ownership to the next waiter (WAIT_ABANDONED), so there is no stale
    lock. On wait-timeout we proceed anyway (URLs are needed and 3 min means
    something is wedged, not merely busy). No-op off Windows."""
    if os.name != "nt":
        yield True
        return
    import ctypes
    from ctypes import wintypes

    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    handle = k.CreateMutexW(None, False, _GUI_MUTEX_NAME)
    owned = False
    try:
        if handle:
            res = k.WaitForSingleObject(handle, wait_ms)
            owned = res in (0x0, 0x80)  # WAIT_OBJECT_0 / WAIT_ABANDONED
            if not owned:
                log.warning("WebRequest GUI lock: wait timed out (%d ms); proceeding", wait_ms)
        else:
            log.warning("WebRequest GUI lock: CreateMutex failed; proceeding unlocked")
        yield owned
    finally:
        if handle:
            if owned:
                k.ReleaseMutex(handle)
            k.CloseHandle(handle)


def _resolve_script(script: str) -> str:
    """Only run a plain-named .au3 that actually exists in AUTOIT_DIR (the
    read-only, repo-controlled mount) — no path separators, no traversal."""
    if os.path.basename(script) != script or not script.lower().endswith(".au3"):
        raise ValueError(f"bad script name: {script}")
    path = os.path.join(AUTOIT_DIR, script)
    if not os.path.exists(path):
        raise ValueError(f"script not found: {script}")
    return path


def available() -> bool:
    """True only on Windows with the AutoIt interpreter present (i.e. the VM).
    The .exe ships in the repo for all platforms, but only runs under Windows,
    so gate on the OS too — elsewhere the caller uses the common.ini fallback."""
    return os.name == "nt" and os.path.exists(AUTOIT_EXE)


def _window_match() -> str:
    """A token guaranteed to appear in this terminal's window title: the login
    (read lock-free from mt5start.ini). Falls back to the broker name."""
    try:
        with open(INI_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("login="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        return val
    except OSError:
        pass
    return BROKER


def _terminal_pid() -> int:
    """PID of THIS terminal's terminal64.exe, resolved by executable path.

    The window title (login) is ambiguous — cloned terminals of the same
    account have identical titles — so the pid is what actually pins the right
    window for the AutoIt script. WMI via PowerShell, because it can read exe
    paths of elevated processes. Returns 0 if not found (script falls back to
    title matching)."""
    if ACCOUNT:
        path_filter = f"*\\{BROKER}\\{ACCOUNT}\\{INSTANCE}\\*"
    else:
        path_filter = f"*\\{BROKER}\\*"
    ps_cmd = (
        "Get-WmiObject Win32_Process -Filter \"Name='terminal64.exe'\" "
        "| Where-Object { $_.ExecutablePath -like '" + path_filter + "' } "
        "| Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=30,
        )
        pid = int(result.stdout.strip())
        return pid if pid > 0 else 0
    except (ValueError, subprocess.SubprocessError, OSError):
        log.warning("WebRequest: terminal pid lookup failed; falling back to title match")
        return 0


def _config_path(name: str) -> str:
    return os.path.join(TERMINAL_DIR, "Config", name)


def _wait_result(logpath: str, timeout: float) -> tuple[str, str]:
    """Poll the AutoIt log for a ``RESULT=<STATUS>`` line. Returns (status, log)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        try:
            with open(logpath, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
        except OSError:
            continue
        m = re.search(r"RESULT=(\w+)", txt)
        if m:
            return m.group(1), txt
    try:
        with open(logpath, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
    except OSError:
        txt = ""
    return "TIMEOUT", txt


def _run_script(
    script: str, extra_args: list[str], timeout: float, use_runas: bool = False
) -> tuple[str, str]:
    if not available():
        raise RuntimeError(f"AutoIt interpreter not found at {AUTOIT_EXE}")
    script_path = _resolve_script(script)

    logpath = _config_path(_LOG_NAME)
    try:
        os.remove(logpath)
    except OSError:
        pass

    match = _window_match()
    pid = _terminal_pid()
    # AutoIt argv: match, pid, extra..., logpath
    au3_args = [match, str(pid), *extra_args, logpath]
    log.info("AutoIt: launching %s (match=%s, pid=%d, use_runas=%s)",
             script, match, pid, use_runas)

    # Hold the machine-wide GUI mutex for the whole run so no other terminal's
    # apply steals focus mid-type.
    with _gui_lock():
        dbg = ""
        if use_runas:
            # MT5 runs elevated; UIPI blocks a non-elevated process from sending
            # it input. Launch elevated (async — no exit code) and poll the log.
            ps_args = ",".join("'%s'" % a for a in [script_path, *au3_args])
            ps = (
                f"Start-Process '{AUTOIT_EXE}' -ArgumentList {ps_args} "
                "-Verb RunAs -WindowStyle Hidden"
            )
            subprocess.Popen(["powershell", "-Command", ps])
            status, txt = _wait_result(logpath, timeout)
        else:
            # Direct, blocking — captures exit code + stderr for diagnostics.
            cmd = [AUTOIT_EXE, script_path, *au3_args]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                dbg = f"[rc={proc.returncode} stderr={proc.stderr.strip()[:300]}]"
            except subprocess.TimeoutExpired:
                dbg = "[rc=TIMEOUT]"
            try:
                with open(logpath, "r", encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
            except OSError:
                txt = ""
            m = re.search(r"RESULT=(\w+)", txt)
            status = m.group(1) if m else "NORESULT"

    txt = (txt + "\n" + dbg).strip()
    log.info("AutoIt: %s -> RESULT=%s %s", script, status, dbg)
    if status not in ("OK",):
        log.warning("AutoIt %s log tail:\n%s", script, txt[-800:])
    return status, txt


def apply_urls(urls: list[str], timeout: float = 120, use_runas: bool = False) -> tuple[str, str]:
    """Set the given allowlist in the running terminal via the Options dialog.
    Returns (status, autoit_log). status == 'OK' on success."""
    urlfile = _config_path(_URLS_NAME)
    os.makedirs(os.path.dirname(urlfile), exist_ok=True)
    with open(urlfile, "w", encoding="utf-8") as f:
        f.write("\n".join(urls))
    return _run_script("set_webrequest.au3", [urlfile], timeout, use_runas)


def run_named(script: str, timeout: float = 60, use_runas: bool = False) -> tuple[str, str]:
    """Dev helper: run an arbitrary repo-shipped .au3 (e.g. the inspector)."""
    return _run_script(script, [], timeout, use_runas)
