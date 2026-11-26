"""The broker server's UTC offset, static or measured from live ticks.

MT5 stamps ticks, bars, orders and deals with the broker server's wall clock
disguised as unix seconds. Most brokers move that clock twice a year, so a
static `utc_offset` is an hour wrong for half the year. With `utc_offset: auto`
this module works the offset out instead:

- The reference clock is the Docker host, read from the HTTP `Date` header of
  the wickworks sidecar, because the guest's own clock is only as good as its
  last time sync (Windows' internet sync fails in the VM). The guest clock is
  the fallback, and the status says which one was used.
- A measurement reads the latest tick of a few busy near-24h symbols and
  rounds `tick time - reference UTC` to 30 minutes. `decide` only accepts it
  when the symbols agree, the freshest tick is close to a whole step (a stale
  tick from a closed market is not), the value is inside +-14 h, and a change
  from a known value is exactly one hour (a daylight saving step). Any other
  change needs CONFIRMATIONS_FOR_OTHER_JUMPS identical readings in a row.
- A change from a known value also needs CONFIRMATIONS_FOR_CHANGE readings in a
  row, so one odd reading never shifts every timestamp.
- The accepted value is saved next to the terminal and loaded on the next
  start, so a restart on a weekend keeps the right value until the market
  opens.

Every conversion in mt5client reads offset_seconds(). The value is one int
replaced atomically, so a request never sees half an update.
"""

from __future__ import annotations

import email.utils
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from mt5api.config import (
    TERMINAL_DIR,
    UTC_OFFSET_AUTO,
    UTC_OFFSET_SECONDS,
    WICKWORKS_URL,
)
from mt5api.logger import log

# Broker offsets are whole hours almost everywhere and half hours at most, so
# readings round to this step.
OFFSET_STEP_SECONDS = 1800
# A fresh tick lands this close to a whole step: request latency plus the
# reference clock's one-second resolution plus normal quote gaps. A tick from
# a closed market is minutes to days old and misses it.
MAX_RESIDUAL_SECONDS = 300
# Real offsets run from UTC-12 to UTC+14.
MAX_ABS_OFFSET_SECONDS = 14 * 3600
# The size of a daylight saving step.
DST_STEP_SECONDS = 3600
# Readings in a row that must agree before a known value changes by one DST step.
CONFIRMATIONS_FOR_CHANGE = 2
# Readings in a row that must agree before any other jump is accepted, so a
# wrong saved value cannot stick forever.
CONFIRMATIONS_FOR_OTHER_JUMPS = 3
# Symbols that quote almost around the clock; the first matches the broker
# lists (with any suffix) are measured. Two agreeing is the normal bar.
MEASURE_SYMBOL_PREFIXES = ("EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "XAUUSD")
MIN_AGREEING_SYMBOLS = 2
# How often the measuring thread runs. A measurement is a handful of tick
# reads under the SDK lock, so every few minutes costs nothing and picks up a
# broker's clock change within minutes of the weekly open.
MEASURE_INTERVAL_SECONDS = 300
# Until the first good reading, try again sooner (the terminal may still be
# logging in).
RETRY_INTERVAL_SECONDS = 60
# The reference clock request must not hold up a measurement.
REFERENCE_TIMEOUT_SECONDS = 3
STATE_BASENAME = "broker_utc_offset.json"

MODE_STATIC = "static"
MODE_AUTO = "auto"
STATUS_STATIC = "static"
STATUS_SAVED = "saved"
STATUS_FALLBACK = "fallback"
STATUS_MEASURED = "measured"
CLOCK_HOST = "host"
CLOCK_VM = "vm"


@dataclass
class History:
    """Readings that were not applied yet, for the confirmation rules."""

    pending: int | None = None
    count: int = 0
    log: list = field(default_factory=list)


_lock = threading.Lock()
_offset: int = UTC_OFFSET_SECONDS
_status: str = STATUS_STATIC
_measured_at: float | None = None
_clock: str | None = None
_last_error: str | None = None
_history = History()


def offset_seconds() -> int:
    """The broker's current UTC offset in seconds."""
    return _offset


def snapshot() -> dict:
    """What GET /terminal reports about the offset."""
    with _lock:
        return {
            "broker_utc_offset_hours": _offset / 3600.0,
            "broker_utc_offset_seconds": _offset,
            "broker_utc_offset_mode": MODE_AUTO if UTC_OFFSET_AUTO else MODE_STATIC,
            "broker_utc_offset_status": _status,
            "broker_utc_offset_measured_at": _measured_at,
            "broker_utc_offset_clock": _clock,
            "broker_utc_offset_error": _last_error,
        }


def state_path() -> str:
    return os.path.join(TERMINAL_DIR, STATE_BASENAME)


def _load_saved(path: str) -> int | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        value = int(data["offset_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if abs(value) > MAX_ABS_OFFSET_SECONDS or value % OFFSET_STEP_SECONDS:
        log.warning("Ignoring saved broker UTC offset %r in %s", value, path)
        return None
    return value


def _save(path: str, value: int, measured_at: float, clock: str) -> None:
    """Write the accepted value atomically. Best effort: a failed save only
    costs the next start its head start."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"offset_seconds": value, "measured_at": measured_at, "clock": clock}, fh)
        os.replace(tmp, path)
    except OSError as err:
        log.warning("Could not save the broker UTC offset to %s: %s", path, err)


def init(path: str | None = None) -> None:
    """Set the starting value: the static offset, or for auto the saved value,
    else 0 until the first measurement."""
    global _offset, _status
    with _lock:
        if not UTC_OFFSET_AUTO:
            _offset, _status = UTC_OFFSET_SECONDS, STATUS_STATIC
            return
        saved = _load_saved(path or state_path())
        if saved is None:
            _offset, _status = 0, STATUS_FALLBACK
            log.warning("Broker UTC offset is auto and nothing is saved yet; "
                        "using 0 until the first measurement.")
            return
        _offset, _status = saved, STATUS_SAVED
        log.info("Broker UTC offset is auto; starting from the saved %+.1fh.", saved / 3600)


def reference_utc_delta(url: str = WICKWORKS_URL,
                        timeout: float = REFERENCE_TIMEOUT_SECONDS) -> tuple[float, str]:
    """Seconds to add to the guest clock to get host UTC, and which clock that is.

    The host's time comes from the `Date` header of a HEAD request to the
    wickworks sidecar, which runs on the Docker host. The header has one-second
    resolution and is truncated, so half a second is added, and the request's
    midpoint stands for the moment it was taken. Falls back to the guest clock
    (delta 0) when the sidecar does not answer or sends no usable header."""
    try:
        request = urllib.request.Request(url, method="HEAD")
        before = time.time()
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            header = resp.headers.get("Date")
        after = time.time()
    except Exception as err:  # noqa: BLE001 - any failure falls back to the guest clock
        log.debug("Reference clock unavailable (%s); using the guest clock.", err)
        return 0.0, CLOCK_VM
    try:
        host = email.utils.parsedate_to_datetime(header).timestamp() + 0.5
    except (TypeError, ValueError):
        return 0.0, CLOCK_VM
    return host - (before + after) / 2, CLOCK_HOST


def round_offset(raw: float) -> int:
    return int(round(raw / OFFSET_STEP_SECONDS)) * OFFSET_STEP_SECONDS


def decide(readings: list[float], current: int, status: str,
           history: History) -> tuple[int | None, str]:
    """Pick the offset a set of raw readings supports, or None with the reason.

    `readings` are `tick time - reference UTC` in seconds, one per symbol.
    Updates `history` for the confirmation rules."""
    if not readings:
        return None, "no ticks"
    freshest = max(readings)
    candidate = round_offset(freshest)
    if abs(freshest - candidate) > MAX_RESIDUAL_SECONDS:
        return None, "stale ticks (market closed?)"
    if abs(candidate) > MAX_ABS_OFFSET_SECONDS:
        return None, "reading outside +-14h"
    agreeing = sum(
        1 for r in readings
        if round_offset(r) == candidate and abs(r - candidate) <= MAX_RESIDUAL_SECONDS
    )
    if agreeing < min(MIN_AGREEING_SYMBOLS, len(readings)):
        return None, "symbols disagree"

    if candidate == current and status in (STATUS_MEASURED, STATUS_SAVED, STATUS_STATIC):
        history.pending, history.count = None, 0
        return candidate, "unchanged"
    if status == STATUS_FALLBACK:
        history.pending, history.count = None, 0
        return candidate, "first measurement"

    if candidate == history.pending:
        history.count += 1
    else:
        history.pending, history.count = candidate, 1
    needed = (CONFIRMATIONS_FOR_CHANGE if abs(candidate - current) == DST_STEP_SECONDS
              else CONFIRMATIONS_FOR_OTHER_JUMPS)
    if history.count < needed:
        return None, f"change to {candidate:+d}s seen {history.count}/{needed} times"
    history.pending, history.count = None, 0
    return candidate, "changed"


def _measure_symbols(mt5, call) -> list[str]:
    """The shortest broker name for each preferred prefix that exists."""
    found = []
    # The group filter keeps this to a few symbols on brokers that list thousands.
    group = ",".join(f"*{prefix}*" for prefix in MEASURE_SYMBOL_PREFIXES)
    symbols = call(mt5.symbols_get, group=group) or ()
    names = [s.name for s in symbols if getattr(s, "name", None)]
    for prefix in MEASURE_SYMBOL_PREFIXES:
        matches = sorted((n for n in names if n.upper().startswith(prefix)), key=len)
        if matches:
            found.append(matches[0])
    return found


def read_offsets(mt5, call, delta: float) -> list[float]:
    """`tick time - reference UTC` for each measured symbol with a tick."""
    readings = []
    for name in _measure_symbols(mt5, call):
        call(mt5.symbol_select, name, True)
        tick = call(mt5.symbol_info_tick, name)
        stamp = getattr(tick, "time_msc", 0) or 0
        broker = stamp / 1000.0 if stamp else float(getattr(tick, "time", 0) or 0)
        if broker <= 0:
            continue
        readings.append(broker - (time.time() + delta))
    return readings


def apply(value: int, reason: str, clock: str, path: str | None = None) -> None:
    global _offset, _status, _measured_at, _clock, _last_error
    now = time.time()
    with _lock:
        old = _offset
        _offset, _status, _measured_at, _clock, _last_error = (
            value, STATUS_MEASURED, now, clock, None)
    if value != old:
        log.warning("Broker UTC offset changed: %+.1fh -> %+.1fh (%s, %s clock)",
                    old / 3600, value / 3600, reason, clock)
    _save(path or state_path(), value, now, clock)


def measure_once() -> None:
    """One measurement under the SDK lock, applied when decide() accepts it."""
    global _last_error
    import MetaTrader5 as mt5

    from mt5api.mt5client import ensure_initialized, m, session

    delta, clock = reference_utc_delta()
    with session(bypass_queue_cap=True):
        if not ensure_initialized():
            readings = []
        else:
            readings = read_offsets(mt5, m, delta)
    with _lock:
        current, status = _offset, _status
    value, reason = decide(readings, current, status, _history)
    if value is None:
        with _lock:
            _last_error = reason
        log.info("Broker UTC offset not updated: %s (readings %s, %s clock)",
                 reason, [round(r) for r in readings], clock)
        return
    apply(value, reason, clock)


def _loop() -> None:
    while True:
        try:
            measure_once()
        except Exception:  # noqa: BLE001 - never kill the measuring thread
            log.exception("Broker UTC offset measurement failed")
        with _lock:
            measured = _status == STATUS_MEASURED and _last_error is None
        time.sleep(MEASURE_INTERVAL_SECONDS if measured else RETRY_INTERVAL_SECONDS)


def start() -> None:
    """Start measuring in the background when the offset is auto."""
    if not UTC_OFFSET_AUTO:
        return
    threading.Thread(target=_loop, daemon=True, name="broker-utc-offset").start()
    log.info("Broker UTC offset is auto: measuring every %ds.", MEASURE_INTERVAL_SECONDS)


init()
