from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.mt5client import (
    ensure_initialized,
    to_dict,
    utc_seconds_to_broker_dt,
)


def _parse_range():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    if not date_from or not date_to:
        return None, None, (
            jsonify({"error": "'from' and 'to' query params required (unix timestamps)"}),
            400,
        )
    try:
        df = utc_seconds_to_broker_dt(date_from)
        dt = utc_seconds_to_broker_dt(date_to)
    except (TypeError, ValueError):
        return None, None, (
            jsonify({"error": "'from' and 'to' must be unix timestamps"}),
            400,
        )
    return df, dt, None


def get_orders():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    df, dt, err = _parse_range()
    if err is not None:
        return err
    orders = mt5.history_orders_get(df, dt)
    return jsonify([to_dict(o) for o in orders] if orders else [])


def get_deals():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    df, dt, err = _parse_range()
    if err is not None:
        return err
    deals = mt5.history_deals_get(df, dt)
    return jsonify([to_dict(d) for d in deals] if deals else [])
