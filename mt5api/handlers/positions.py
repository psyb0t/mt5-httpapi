from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.mt5client import ensure_initialized, to_dict


def list_positions():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    symbol = request.args.get("symbol")
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    return jsonify([to_dict(p) for p in positions] if positions else [])


def get_position(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return jsonify({"error": f"Position {ticket} not found"}), 404
    return jsonify(to_dict(positions[0]))


def update_position(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    body = request.get_json()
    if not body:
        return jsonify({"error": "Request body required"}), 400

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return jsonify({"error": f"Position {ticket} not found"}), 404
    pos = positions[0]

    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": pos.symbol,
        "sl": float(body.get("sl", pos.sl)),
        "tp": float(body.get("tp", pos.tp)),
    }

    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(mt5.last_error())
        return jsonify({"error": f"Failed to update position: {err}"}), 500
    return jsonify(to_dict(result))


def close_position(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return jsonify({"error": f"Position {ticket} not found"}), 404
    pos = positions[0]

    body = request.get_json(silent=True)
    volume = float(body.get("volume", pos.volume)) if body else pos.volume

    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": pos.symbol,
        "volume": volume,
        "type": close_type,
        "price": price,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "deviation": int(body.get("deviation", 20)) if body else 20,
    }

    result = mt5.order_send(req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(mt5.last_error())
        return jsonify({"error": f"Failed to close position: {err}"}), 500
    return jsonify(to_dict(result))
