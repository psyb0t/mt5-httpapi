from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import ORDER_TYPE_MAP, TIME_MAP
from mt5api.mt5client import ensure_initialized, to_dict, build_order_request


def list_orders():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    symbol = request.args.get("symbol")
    orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    return jsonify([to_dict(o) for o in orders] if orders else [])


def create_order():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    body = request.get_json()
    if not body:
        return jsonify({"error": "Request body required"}), 400

    for field in ("symbol", "type", "volume"):
        if field not in body:
            return jsonify({"error": f"'{field}' is required"}), 400

    order_type = body["type"].upper()
    if order_type not in ORDER_TYPE_MAP:
        return jsonify({"error": f"Invalid type: {order_type}. Use: {list(ORDER_TYPE_MAP.keys())}"}), 400

    is_market = order_type in ("BUY", "SELL")

    if is_market and "price" not in body:
        tick = mt5.symbol_info_tick(body["symbol"])
        if tick is None:
            return jsonify({"error": f"Cannot get price for {body['symbol']}"}), 500
        body["price"] = tick.ask if order_type == "BUY" else tick.bid

    body["action"] = "DEAL" if is_market else "PENDING"

    req, err = build_order_request(body)
    if err:
        return jsonify({"error": err}), 400

    result = mt5.order_send(req)
    if result is None:
        err = mt5.last_error()
        return jsonify({"error": f"order_send failed: {err}"}), 500

    status = 201 if result.retcode == mt5.TRADE_RETCODE_DONE else 200
    return jsonify(to_dict(result)), status


def get_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404
    return jsonify(to_dict(orders[0]))


def update_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    body = request.get_json()
    if not body:
        return jsonify({"error": "Request body required"}), 400

    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404
    order = orders[0]

    req = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "symbol": order.symbol,
        "price": float(body.get("price", order.price_open)),
        "sl": float(body.get("sl", order.sl)),
        "tp": float(body.get("tp", order.tp)),
        "type_time": order.type_time,
        "expiration": order.expiration,
    }

    if "type_time" in body:
        tt = body["type_time"].upper()
        if tt in TIME_MAP:
            req["type_time"] = TIME_MAP[tt]

    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(mt5.last_error())
        return jsonify({"error": f"Failed to modify order: {err}"}), 500
    return jsonify(to_dict(result))


def cancel_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404

    req = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }

    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(mt5.last_error())
        return jsonify({"error": f"Failed to cancel order: {err}"}), 500
    return jsonify(to_dict(result))
