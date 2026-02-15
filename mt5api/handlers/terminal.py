from flask import jsonify

import MetaTrader5 as mt5

from mt5api.mt5client import ensure_initialized, to_dict


def ping():
    return jsonify({"status": "ok"})


def last_error():
    err = mt5.last_error()
    return jsonify({"code": err[0], "message": err[1]})


def get_terminal():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    return jsonify(to_dict(mt5.terminal_info()))


def init():
    if not ensure_initialized():
        err = mt5.last_error()
        return jsonify({"success": False, "error": str(err)}), 500
    return jsonify({"success": True})


def shutdown():
    mt5.shutdown()
    return jsonify({"success": True})
