import MetaTrader5 as mt5
from flask import jsonify
from mt5api.config import MODE, UTC_OFFSET_HOURS, UTC_OFFSET_SECONDS
from mt5api.mt5client import (
    ensure_initialized,
    m,
    restart_terminal,
    sdk_worker_snapshot,
    to_dict,
    with_mt5,
)


def ping():
    # Deliberately does not take the MT5 lock (no @with_mt5) — this is the
    # container healthcheck route and must answer even while every SDK call
    # is wedged. sdk_worker_snapshot() only takes its own small lock.
    count, oldest_age = sdk_worker_snapshot()
    return jsonify({
        "status": "ok",
        "mode": MODE,
        "sdk_threads_alive": count,
        "sdk_oldest_alive_s": round(oldest_age, 1) if oldest_age is not None else None,
    })


@with_mt5
def last_error():
    err = m(mt5.last_error)
    return jsonify({"code": err[0], "message": err[1]})


@with_mt5
def get_terminal():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    info = to_dict(m(mt5.terminal_info))
    if info is not None:
        info["broker_utc_offset_hours"] = UTC_OFFSET_HOURS
        info["broker_utc_offset_seconds"] = UTC_OFFSET_SECONDS
    return jsonify(info)


@with_mt5
def init():
    if not ensure_initialized():
        err = m(mt5.last_error)
        return jsonify({"success": False, "error": str(err)}), 500
    return jsonify({"success": True})


@with_mt5
def shutdown():
    m(mt5.shutdown)
    return jsonify({"success": True})


@with_mt5
def restart():
    if not restart_terminal():
        return jsonify({"success": False, "error": "Terminal restart failed"}), 500
    return jsonify({"success": True})
