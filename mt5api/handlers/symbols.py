from datetime import datetime, timezone

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import TIMEFRAME_MAP
from mt5api.mt5client import ensure_initialized, to_dict


def list_symbols():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    group = request.args.get("group")
    syms = mt5.symbols_get(group=group) if group else mt5.symbols_get()
    return jsonify([s.name for s in syms] if syms else [])


def get_symbol(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    info = mt5.symbol_info(symbol)
    if info is None:
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    return jsonify(to_dict(info))


def get_tick(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return jsonify({"error": f"No tick for {symbol}"}), 404
    return jsonify(to_dict(tick))


def get_rates(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    tf_str = request.args.get("timeframe", "M1").upper()
    count = int(request.args.get("count", 100))

    timeframe = TIMEFRAME_MAP.get(tf_str)
    if timeframe is None:
        return jsonify({"error": f"Invalid timeframe: {tf_str}. Use: {list(TIMEFRAME_MAP.keys())}"}), 400

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
    count = int(request.args.get("count", 100))

    ticks = mt5.copy_ticks_from(
        symbol, datetime(2000, 1, 1, tzinfo=timezone.utc),
        count, mt5.COPY_TICKS_ALL,
    )
    if ticks is None or len(ticks) == 0:
        return jsonify([])

    return jsonify([{
        "time": int(t[0]), "bid": float(t[1]), "ask": float(t[2]),
        "last": float(t[3]), "volume": int(t[4]), "time_msc": int(t[5]),
        "flags": int(t[6]), "volume_real": float(t[7]),
    } for t in ticks])
