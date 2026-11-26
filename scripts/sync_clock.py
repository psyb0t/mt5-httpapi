"""Set the Windows guest clock from the Docker host's clock.

    sync_clock.py <config.yaml> <start.log> <full.log>

Windows' own internet time sync cannot resolve time.windows.com from the VM
(Time-Service event 134 on every boot), so nothing corrected the guest clock
when it drifted, and every timestamp the API and the event log wrote drifted
with it. The host is NTP-synced, and the wickworks sidecar runs on it, so its
HTTP `Date` header is the host's UTC time to the second. start.bat runs this
once per boot, before any terminal starts, and again every hour from its
status loop.

The guest clock is set (SetSystemTime, the scripts run elevated) only when it
is more than MAX_DRIFT_SECONDS off. When the sidecar does not answer, the
clock is left alone and a warning is logged. The script always exits 0: a
clock problem must never stop a boot.

Runs inside the VM with only the standard library (PyYAML when present, for
the wickworks URL). Keep it ASCII (WIN1, WIN2 apply to what start.bat calls).
"""

from __future__ import annotations

import datetime
import email.utils
import sys
import time
import urllib.error
import urllib.request

# The same default as mt5api.config.WICKWORKS_URL: the dockurr/windows gateway
# that routes to the network namespace wickworks shares with the VM container.
DEFAULT_REFERENCE_URL = "http://20.20.20.1:8000/"
# The Date header has one-second resolution, so a smaller drift cannot be
# measured reliably; two seconds also leaves room for request latency.
MAX_DRIFT_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 5
# The header is truncated to the second, so the true time is half a second later on average.
DATE_HEADER_HALF_SECOND = 0.5


def reference_url(config_path: str) -> str:
    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - a missing or unreadable config uses the default
        return DEFAULT_REFERENCE_URL
    wickworks = cfg.get("wickworks") or {}
    url = wickworks.get("url") if isinstance(wickworks, dict) else None
    return str(url) if url else DEFAULT_REFERENCE_URL


def parse_date_header(header: str | None) -> float | None:
    if not header:
        return None
    try:
        return email.utils.parsedate_to_datetime(header).timestamp() + DATE_HEADER_HALF_SECOND
    except (TypeError, ValueError):
        return None


def host_time_delta(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> float | None:
    """Seconds to add to the guest clock to reach host UTC, or None."""
    try:
        request = urllib.request.Request(url, method="HEAD")
        before = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                header = resp.headers.get("Date")
        except urllib.error.HTTPError as err:
            # Any HTTP answer carries the server's Date, and wickworks answers
            # a HEAD on its root with 405.
            header = err.headers.get("Date")
        after = time.time()
    except Exception:  # noqa: BLE001 - an unreachable sidecar leaves the clock alone
        return None
    host = parse_date_header(header)
    if host is None:
        return None
    return host - (before + after) / 2


def needs_set(delta: float | None) -> bool:
    return delta is not None and abs(delta) > MAX_DRIFT_SECONDS


def set_system_time_utc(target: float) -> bool:
    """Set the Windows clock to `target` unix seconds (UTC)."""
    import ctypes
    from ctypes import wintypes

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(name, wintypes.WORD) for name in (
            "wYear", "wMonth", "wDayOfWeek", "wDay",
            "wHour", "wMinute", "wSecond", "wMilliseconds")]

    moment = datetime.datetime.fromtimestamp(target, tz=datetime.timezone.utc)
    value = SYSTEMTIME(moment.year, moment.month, (moment.isoweekday() % 7), moment.day,
                       moment.hour, moment.minute, moment.second,
                       moment.microsecond // 1000)
    return bool(ctypes.windll.kernel32.SetSystemTime(ctypes.byref(value)))


def log_line(paths: list[str], message: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # strict=False: start.bat passes both logs, but a hand run may pass fewer.
    for path, tag in zip(paths, ("", "[clock] "), strict=False):
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] {tag}{message}\n")
        except OSError:
            pass


def main(argv: list[str]) -> int:
    config_path = argv[1] if len(argv) > 1 else ""
    logs = argv[2:4]
    url = reference_url(config_path)
    delta = host_time_delta(url)
    if delta is None:
        log_line(logs, f"WARN: clock sync: no Date header from {url}; guest clock left as is.")
        return 0
    if not needs_set(delta):
        return 0
    before = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if set_system_time_utc(time.time() + delta):
        after = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        log_line(logs, f"clock sync: guest clock was {delta:+.1f}s off; {before} -> {after}")
    else:
        log_line(logs, f"WARN: clock sync: SetSystemTime failed; guest clock {delta:+.1f}s off.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
