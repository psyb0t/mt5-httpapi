import MetaTrader5 as mt5
from flask import jsonify
from mt5api import broker_clock
from mt5api.config import MODE
from mt5api.mt5client import (
    ensure_initialized,
    m,
    restart_terminal,
    sdk_worker_snapshot,
    to_dict,
    with_mt5,
)


def ping():
    # No @with_mt5: the container healthcheck must answer while SDK calls are
    # stuck, and sdk_worker_snapshot() takes only its own lock.
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
        info.update(broker_clock.snapshot())
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
