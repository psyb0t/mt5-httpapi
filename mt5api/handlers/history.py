from datetime import datetime, timezone

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.mt5client import ensure_initialized, to_dict


def get_orders():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    if not date_from or not date_to:
        return jsonify({"error": "'from' and 'to' query params required (unix timestamps)"}), 400
    orders = mt5.history_orders_get(
        datetime.fromtimestamp(int(date_from), tz=timezone.utc),
        datetime.fromtimestamp(int(date_to), tz=timezone.utc),
    )
    return jsonify([to_dict(o) for o in orders] if orders else [])


def get_deals():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    if not date_from or not date_to:
        return jsonify({"error": "'from' and 'to' query params required (unix timestamps)"}), 400
    deals = mt5.history_deals_get(
        datetime.fromtimestamp(int(date_from), tz=timezone.utc),
        datetime.fromtimestamp(int(date_to), tz=timezone.utc),
    )
    return jsonify([to_dict(d) for d in deals] if deals else [])
