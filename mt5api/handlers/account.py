import MetaTrader5 as mt5
from flask import jsonify

from mt5api.mt5client import ensure_initialized, to_dict


def get_account():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    info = mt5.account_info()
    if info is None:
        return jsonify({"error": "No account logged in"}), 404
    return jsonify(to_dict(info))
