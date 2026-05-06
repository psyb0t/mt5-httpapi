from datetime import datetime, timezone

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import TIMEFRAME_MAP, TIMEFRAME_SECONDS
from mt5api.mt5client import (
    broker_to_utc_ms,
    broker_to_utc_seconds,
    ensure_initialized,
    m,
    to_dict,
    utc_seconds_to_broker_dt,
    with_mt5,
)

TICK_FLAGS_MAP = {
    "ALL": mt5.COPY_TICKS_ALL,
    "INFO": mt5.COPY_TICKS_INFO,
    "TRADE": mt5.COPY_TICKS_TRADE,
}

# Retry-doubling budgets. The MT5 SDK has no native "N forward bars from
# date" or "N backward ticks from date" call, so both branches fake it
# with copy_*_range over a guessed window. Start tight, double on
# shortfall, cap attempts so a pathological symbol doesn't loop forever.
RATES_FORWARD_INITIAL_MULT = 1.5  # covers weekend/holiday gaps on intraday
RATES_FORWARD_MAX_ATTEMPTS = 4    # final window: 1.5 * 2^3 = 12x
TICKS_BACKWARD_INITIAL_SEC_PER_TICK = 0.1  # ~10 ticks/sec assumption
TICKS_BACKWARD_FLOOR_WINDOW_SEC = 60       # min window for sparse symbols
TICKS_BACKWARD_MAX_ATTEMPTS = 6   # final window: floor * 2^5


def _parse_anchor(s):
    """Parse a `from`/`to` query value into real-UTC unix seconds.

    Accepted forms:
      - bare integer seconds (may be negative)
      - 'YYYY_MM_DD_HH_MM_SS' — explicit datetime, interpreted as real UTC
      - 'YYYY_MM_DD' — date only, time defaults to 00:00:00 UTC

    Returns int unix seconds, or None on parse failure.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Bare integer (no underscores — Python's int() accepts '2024_01_15' as
    # a numeric literal with digit separators, which would mis-parse our
    # date format).
    if "_" not in s:
        try:
            return int(s)
        except ValueError:
            pass
    parts = s.split("_")
    try:
        if len(parts) == 6:
            y, mo, d, h, mi, se = (int(p) for p in parts)
            dt = datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
        elif len(parts) == 3:
            y, mo, d = (int(p) for p in parts)
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
        else:
            return None
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _ensure_symbol(symbol):
    """Make sure symbol is selected in MarketWatch. Returns True if known."""
    info = m(mt5.symbol_info, symbol)
    if info is None:
        return False
    if not info.visible:
        m(mt5.symbol_select, symbol, True)
    return True


@with_mt5
def list_symbols():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    group = request.args.get("group")
    syms = m(mt5.symbols_get, group=group) if group else m(mt5.symbols_get)
    return jsonify([s.name for s in syms] if syms else [])


@with_mt5
def get_symbol(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    info = m(mt5.symbol_info, symbol)
    if info is None:
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    return jsonify(to_dict(info))


@with_mt5
def get_tick(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    tick = m(mt5.symbol_info_tick, symbol)
    if tick is None:
        return jsonify({"error": f"No tick for {symbol}"}), 404
    return jsonify(to_dict(tick))


@with_mt5
def get_rates(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    tf_str = request.args.get("timeframe", "M1").upper()
    timeframe = TIMEFRAME_MAP.get(tf_str)
    if timeframe is None:
        return jsonify({"error": f"Invalid timeframe: {tf_str}. Use: {list(TIMEFRAME_MAP.keys())}"}), 400

    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404

    date_from = request.args.get("from")
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    # Three modes: range (from+to), anchor+count (from+count or count alone),
    # or default (count=100 from now). count and to are mutually exclusive.
    if date_to is not None and count_arg is not None:
        return jsonify({"error": "use either 'count' or 'to', not both"}), 400
    if date_to is not None and date_from is None:
        return jsonify({"error": "'to' requires 'from'"}), 400

    if date_to is not None:
        from_utc = _parse_anchor(date_from)
        to_utc = _parse_anchor(date_to)
        if from_utc is None or to_utc is None:
            return jsonify({"error": "'from'/'to' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(from_utc)
        dt = utc_seconds_to_broker_dt(to_utc)
        rates = m(mt5.copy_rates_range, symbol, timeframe, df, dt)
        if rates is None or len(rates) == 0:
            return jsonify([])
        return jsonify([{
            "time": broker_to_utc_seconds(r[0]),
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5]),
            "spread": int(r[6]), "real_volume": int(r[7]),
        } for r in rates])

    count = int(count_arg) if count_arg else 100

    # count > 0: N bars forward from `from` (inclusive)
    # count < 0: |N| bars backward ending at `from` (inclusive)
    # from omitted: anchor is now
    if count == 0:
        return jsonify([])

    abs_count = abs(count)

    if date_from:
        anchor_utc = _parse_anchor(date_from)
        if anchor_utc is None:
            return jsonify({"error": "'from' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(anchor_utc)
        if count > 0:
            # Forward from anchor (inclusive): MT5's copy_rates_from goes
            # BACKWARD from a date. Fake forward-by-count with a windowed
            # range query, starting tight and doubling on shortfall.
            tf_secs = TIMEFRAME_SECONDS.get(tf_str, 60)
            mult = RATES_FORWARD_INITIAL_MULT
            rates = []
            for _ in range(RATES_FORWARD_MAX_ATTEMPTS):
                end_utc = anchor_utc + int(abs_count * tf_secs * mult) + tf_secs
                end_dt = utc_seconds_to_broker_dt(end_utc)
                got = m(mt5.copy_rates_range, symbol, timeframe, df, end_dt)
                if got is None:
                    rates = []
                    break
                rates = [r for r in got
                         if broker_to_utc_seconds(r[0]) >= anchor_utc]
                if len(rates) >= abs_count:
                    rates = rates[:abs_count]
                    break
                mult *= 2
        else:
            # Backward ending at anchor (inclusive): copy_rates_from natively
            # returns abs_count bars whose time is at-or-before anchor.
            rates = m(mt5.copy_rates_from, symbol, timeframe, df, abs_count)
    else:
        # No `from` — last N bars from current bar
        rates = m(mt5.copy_rates_from_pos, symbol, timeframe, 0, abs_count)

    if rates is None or len(rates) == 0:
        return jsonify([])

    return jsonify([{
        "time": broker_to_utc_seconds(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5]),
        "spread": int(r[6]), "real_volume": int(r[7]),
    } for r in rates])


@with_mt5
def get_ticks(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404

    flags_str = request.args.get("flags", "ALL").upper()
    flags = TICK_FLAGS_MAP.get(flags_str)
    if flags is None:
        return jsonify({"error": f"Invalid flags: {flags_str}. Use: {list(TICK_FLAGS_MAP.keys())}"}), 400

    date_from = request.args.get("from")
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    # Three modes: range (from+to), anchor+count (from+count or count alone),
    # or default (count=100 from now). count and to are mutually exclusive.
    if date_to is not None and count_arg is not None:
        return jsonify({"error": "use either 'count' or 'to', not both"}), 400
    if date_to is not None and date_from is None:
        return jsonify({"error": "'to' requires 'from'"}), 400

    if date_to is not None:
        from_utc = _parse_anchor(date_from)
        to_utc = _parse_anchor(date_to)
        if from_utc is None or to_utc is None:
            return jsonify({"error": "'from'/'to' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(from_utc)
        dt = utc_seconds_to_broker_dt(to_utc)
        ticks = m(mt5.copy_ticks_range, symbol, df, dt, flags)
        if ticks is None or len(ticks) == 0:
            return jsonify([])
        return jsonify([{
            "time": broker_to_utc_seconds(t[0]),
            "bid": float(t[1]), "ask": float(t[2]),
            "last": float(t[3]), "volume": int(t[4]),
            "time_msc": broker_to_utc_ms(t[5]),
            "flags": int(t[6]), "volume_real": float(t[7]),
        } for t in ticks])

    count = int(count_arg) if count_arg else 100

    # count > 0: N ticks forward from `from` (inclusive)
    # count < 0: |N| ticks backward ending at `from` (inclusive)
    # from omitted: anchor is now
    if count == 0:
        return jsonify([])

    abs_count = abs(count)

    if date_from:
        anchor_utc = _parse_anchor(date_from)
        if anchor_utc is None:
            return jsonify({"error": "'from' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(anchor_utc)
        if count > 0:
            # Forward from anchor (inclusive). Unlike copy_rates_from,
            # copy_ticks_from natively goes FORWARD: returns abs_count ticks
            # whose time is at-or-after the anchor.
            ticks = m(mt5.copy_ticks_from, symbol, df, abs_count, flags)
        else:
            # Backward ending at anchor: copy_ticks_from natively goes
            # FORWARD, no native backward-by-count. Fake it with a range
            # query, starting tight (~0.1s per tick floor) and doubling
            # the window on shortfall.
            window = max(
                TICKS_BACKWARD_FLOOR_WINDOW_SEC,
                int(abs_count * TICKS_BACKWARD_INITIAL_SEC_PER_TICK),
            )
            ticks = []
            for _ in range(TICKS_BACKWARD_MAX_ATTEMPTS):
                start_utc = max(0, anchor_utc - window)
                df_start = utc_seconds_to_broker_dt(start_utc)
                got = m(mt5.copy_ticks_range, symbol, df_start, df, flags)
                if got is None:
                    ticks = []
                    break
                ticks = [t for t in got
                         if broker_to_utc_seconds(t[0]) <= anchor_utc]
                if len(ticks) >= abs_count:
                    ticks = ticks[-abs_count:]
                    break
                window *= 2
    else:
        ticks = m(
            mt5.copy_ticks_from,
            symbol, datetime(2000, 1, 1, tzinfo=timezone.utc),
            abs_count, flags,
        )

    if ticks is None or len(ticks) == 0:
        return jsonify([])

    return jsonify([{
        "time": broker_to_utc_seconds(t[0]),
        "bid": float(t[1]), "ask": float(t[2]),
        "last": float(t[3]), "volume": int(t[4]),
        "time_msc": broker_to_utc_ms(t[5]),
        "flags": int(t[6]), "volume_real": float(t[7]),
    } for t in ticks])
