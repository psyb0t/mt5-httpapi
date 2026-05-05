from datetime import datetime, timezone

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import TIMEFRAME_MAP, TIMEFRAME_SECONDS
from mt5api.mt5client import (
    broker_to_utc_ms,
    broker_to_utc_seconds,
    ensure_initialized,
    to_dict,
    utc_seconds_to_broker_dt,
)

TICK_FLAGS_MAP = {
    "ALL": mt5.COPY_TICKS_ALL,
    "INFO": mt5.COPY_TICKS_INFO,
    "TRADE": mt5.COPY_TICKS_TRADE,
}


def _parse_unix(s):
    """Parse a real-UTC unix-seconds string to the broker-time datetime that
    MT5 expects for copy_*_range / copy_*_from. Returns None on failure."""
    try:
        return utc_seconds_to_broker_dt(s)
    except (TypeError, ValueError):
        return None


def _ensure_symbol(symbol):
    """Make sure symbol is selected in MarketWatch. Returns True if known."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return False
    if not info.visible:
        mt5.symbol_select(symbol, True)
    return True


def list_symbols():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    group = request.args.get("group")
    syms = mt5.symbols_get(group=group) if group else mt5.symbols_get()
    return jsonify([s.name for s in syms] if syms else [])


def get_symbol(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    info = mt5.symbol_info(symbol)
    if info is None:
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    return jsonify(to_dict(info))


def get_tick(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not _ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return jsonify({"error": f"No tick for {symbol}"}), 404
    return jsonify(to_dict(tick))


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
    count_arg = request.args.get("count")
    count = int(count_arg) if count_arg else 100

    # count > 0: N bars forward from `from` (inclusive)
    # count < 0: |N| bars backward ending at `from` (inclusive)
    # from omitted: anchor is now
    if count == 0:
        return jsonify([])

    abs_count = abs(count)

    if date_from:
        anchor_utc = int(date_from)
        if count > 0:
            df = _parse_unix(date_from)
            if df is None:
                return jsonify({"error": "'from' must be a unix timestamp"}), 400
            rates = mt5.copy_rates_from(symbol, timeframe, df, abs_count)
        else:
            # Walk backward: calculate a start point far enough back,
            # clamp to epoch 0, fetch forward, then take last abs_count.
            tf_secs = TIMEFRAME_SECONDS.get(tf_str, 60)
            start_utc = max(0, anchor_utc - abs_count * tf_secs)
            df = _parse_unix(str(start_utc))
            if df is None:
                return jsonify({"error": "'from' must be a unix timestamp"}), 400
            # Fetch enough bars from the calculated start to cover the window.
            # Ask for 2x to handle gaps (weekends, holidays).
            rates = mt5.copy_rates_from(symbol, timeframe, df, abs_count * 2)
            if rates is not None and len(rates) > 0:
                # Keep only bars at or before the anchor, then last abs_count.
                rates = [r for r in rates
                         if broker_to_utc_seconds(r[0]) <= anchor_utc]
                if len(rates) > abs_count:
                    rates = rates[-abs_count:]
    else:
        # No `from` — last N bars from current bar
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, abs_count)

    if rates is None or len(rates) == 0:
        return jsonify([])

    return jsonify([{
        "time": broker_to_utc_seconds(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5]),
        "spread": int(r[6]), "real_volume": int(r[7]),
    } for r in rates])


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
    count_arg = request.args.get("count")
    count = int(count_arg) if count_arg else 100

    # count > 0: N ticks forward from `from` (inclusive)
    # count < 0: |N| ticks backward ending at `from` (inclusive)
    # from omitted: anchor is now
    if count == 0:
        return jsonify([])

    abs_count = abs(count)

    if date_from:
        df = _parse_unix(date_from)
        if df is None:
            return jsonify({"error": "'from' must be a unix timestamp"}), 400
        anchor_utc = int(date_from)
        if count > 0:
            ticks = mt5.copy_ticks_from(symbol, df, abs_count, flags)
        else:
            # Walk backward: fetch a range ending at anchor, take last abs_count.
            # Use a generous window (1h per tick) to handle weekends/gaps.
            start_utc = max(0, anchor_utc - abs_count * 3600)
            df_start = _parse_unix(str(start_utc))
            ticks = mt5.copy_ticks_range(symbol, df_start, df, flags)
            if ticks is not None and len(ticks) > 0:
                ticks = [t for t in ticks
                         if broker_to_utc_seconds(t[0]) <= anchor_utc]
                if len(ticks) > abs_count:
                    ticks = ticks[-abs_count:]
    else:
        ticks = mt5.copy_ticks_from(
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
