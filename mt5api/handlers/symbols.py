from datetime import datetime, timezone

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import TIMEFRAME_MAP
from mt5api.mt5client import ensure_initialized, to_dict

TICK_FLAGS_MAP = {
    "ALL": mt5.COPY_TICKS_ALL,
    "INFO": mt5.COPY_TICKS_INFO,
    "TRADE": mt5.COPY_TICKS_TRADE,
}


def _parse_unix(s):
    """Parse a unix-seconds string to a UTC datetime. Returns None on failure."""
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
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
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    # Mode resolution:
    # - from + to       -> copy_rates_range
    # - from + count    -> copy_rates_from
    # - count only (or nothing) -> copy_rates_from_pos (last N from current bar)
    if date_from and date_to:
        df = _parse_unix(date_from)
        dt = _parse_unix(date_to)
        if df is None or dt is None:
            return jsonify({"error": "'from' and 'to' must be unix timestamps"}), 400
        if df > dt:
            return jsonify({"error": "'from' must be <= 'to'"}), 400
        rates = mt5.copy_rates_range(symbol, timeframe, df, dt)
    elif date_from:
        df = _parse_unix(date_from)
        if df is None:
            return jsonify({"error": "'from' must be a unix timestamp"}), 400
        count = int(count_arg) if count_arg else 100
        rates = mt5.copy_rates_from(symbol, timeframe, df, count)
    else:
        count = int(count_arg) if count_arg else 100
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)

    if rates is None or len(rates) == 0:
        return jsonify([])

    return jsonify([{
        "time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
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
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    if date_from and date_to:
        df = _parse_unix(date_from)
        dt = _parse_unix(date_to)
        if df is None or dt is None:
            return jsonify({"error": "'from' and 'to' must be unix timestamps"}), 400
        if df > dt:
            return jsonify({"error": "'from' must be <= 'to'"}), 400
        ticks = mt5.copy_ticks_range(symbol, df, dt, flags)
    elif date_from:
        df = _parse_unix(date_from)
        if df is None:
            return jsonify({"error": "'from' must be a unix timestamp"}), 400
        count = int(count_arg) if count_arg else 100
        ticks = mt5.copy_ticks_from(symbol, df, count, flags)
    else:
        count = int(count_arg) if count_arg else 100
        ticks = mt5.copy_ticks_from(
            symbol, datetime(2000, 1, 1, tzinfo=timezone.utc),
            count, flags,
        )

    if ticks is None or len(ticks) == 0:
        return jsonify([])

    return jsonify([{
        "time": int(t[0]), "bid": float(t[1]), "ask": float(t[2]),
        "last": float(t[3]), "volume": int(t[4]), "time_msc": int(t[5]),
        "flags": int(t[6]), "volume_real": float(t[7]),
    } for t in ticks])
