from flask import g, jsonify, request

import re
import time

import MetaTrader5 as mt5

from mt5api.config import ORDER_TYPE_MAP, SPECIFIED_TIME_TYPES, TIME_MAP
from mt5api.logger import log
from mt5api.mt5client import (
    EXPIRATION_REQUIRED_ERROR,
    build_order_request,
    ensure_initialized,
    ensure_symbol,
    m,
    parse_order_expiration,
    parse_order_stoplimit,
    to_dict,
    utc_seconds_to_broker_dt,
    with_mt5,
)

# A client_order_id travels in the order comment, which MT5 caps at 31
# characters. Letters, digits and ._:- survive every broker's comment field
# and need no escaping in logs.
CLIENT_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,31}$")

# How far back a retry is matched against sends and the broker's order
# history. Retries usually come seconds to minutes after a timeout; a week
# also covers an agent that picks a task up again the next day, without
# scanning the account's whole history on every order.
CLIENT_ORDER_ID_LOOKBACK_SECONDS = 7 * 24 * 3600

# The history window ends this far in the future, so a broker clock or a
# configured utc_offset that is off by up to a day still includes the order
# that was just placed.
CLIENT_ORDER_ID_HISTORY_SLACK_SECONDS = 24 * 3600

# Accepted sends by client_order_id: (time sent, trade result). The broker
# lookup finds an order by its comment, but some brokers rewrite or drop
# comments; this catches a retry to the same process even then. Only
# accepted sends are kept: a rejected order was never placed, so a retry with
# the same id must send again. Guarded by the MT5 lock like every handler.
_recent_client_orders = {}


def _accepted(retcode):
    return retcode in (
        mt5.TRADE_RETCODE_DONE,
        mt5.TRADE_RETCODE_PLACED,
        mt5.TRADE_RETCODE_DONE_PARTIAL,
    )


def _forget_old_client_orders(now):
    cutoff = now - CLIENT_ORDER_ID_LOOKBACK_SECONDS
    for cid in [c for c, (sent, _) in _recent_client_orders.items() if sent < cutoff]:
        del _recent_client_orders[cid]


def _find_client_order(cid):
    """Return (source, item) for an order already placed with this
    client_order_id, or (None, None). Checks this process's recent sends,
    then pending orders, open positions and the order history, all by
    comment. Not filtered by symbol: the id names one order on the account."""
    now = time.time()
    _forget_old_client_orders(now)
    recent = _recent_client_orders.get(cid)
    if recent is not None:
        return "recent_send", recent[1]

    for source, items in (
        ("pending_order", m(mt5.orders_get)),
        ("position", m(mt5.positions_get)),
    ):
        for item in items or ():
            if item.comment == cid:
                return source, to_dict(item)

    history = m(
        mt5.history_orders_get,
        utc_seconds_to_broker_dt(now - CLIENT_ORDER_ID_LOOKBACK_SECONDS),
        utc_seconds_to_broker_dt(now + CLIENT_ORDER_ID_HISTORY_SLACK_SECONDS),
    )
    for item in history or ():
        if item.comment == cid:
            return "history_order", to_dict(item)

    return None, None


@with_mt5
def list_orders():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    symbol = request.args.get("symbol")
    orders = m(mt5.orders_get, symbol=symbol) if symbol else m(mt5.orders_get)
    return jsonify([to_dict(o) for o in orders] if orders else [])


@with_mt5
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
        valid_types = list(ORDER_TYPE_MAP.keys())
        return jsonify({"error": f"Invalid type: {order_type}. Use: {valid_types}"}), 400

    cid = body.get("client_order_id")
    if cid is not None:
        if not isinstance(cid, str) or not CLIENT_ORDER_ID_RE.match(cid):
            return jsonify({
                "error": "'client_order_id' must be 1 to 31 letters, digits or ._:-",
            }), 400
        if body.get("comment", cid) != cid:
            return jsonify({
                "error": "'client_order_id' is stored in the order comment; "
                "send no 'comment' or the same value",
            }), 400
        body["comment"] = cid

        source, existing = _find_client_order(cid)
        if source is not None:
            log.info(
                "%s order not sent: client_order_id already used source=%s",
                g.req_id, source,
            )
            replay = existing if source == "recent_send" else {}
            return jsonify({
                **replay,
                "duplicate": True,
                "client_order_id": cid,
                "source": source,
                "existing": existing,
            }), 200

    is_market = order_type in ("BUY", "SELL")

    if not ensure_symbol(body["symbol"]):
        return jsonify({"error": f"Symbol {body['symbol']} not found"}), 404

    if is_market and "price" not in body:
        tick = None
        for _ in range(10):
            tick = m(mt5.symbol_info_tick, body["symbol"])
            if tick is not None and (tick.ask != 0 or tick.bid != 0):
                break
            time.sleep(0.2)
        if tick is None or (tick.ask == 0 and tick.bid == 0):
            return jsonify({"error": f"Cannot get price for {body['symbol']}"}), 500
        body["price"] = tick.ask if order_type == "BUY" else tick.bid

    body["action"] = "DEAL" if is_market else "PENDING"

    req, err = build_order_request(body)
    if err:
        return jsonify({"error": err}), 400

    result = m(mt5.order_send, **req)
    if result is None:
        err = m(mt5.last_error)
        return jsonify({"error": f"order_send failed: {err}"}), 500

    out = to_dict(result)
    if cid is not None:
        out["duplicate"] = False
        out["client_order_id"] = cid
        if _accepted(result.retcode):
            _recent_client_orders[cid] = (time.time(), out)

    status = 201 if result.retcode == mt5.TRADE_RETCODE_DONE else 200
    return jsonify(out), status


@with_mt5
def get_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    orders = m(mt5.orders_get, ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404
    return jsonify(to_dict(orders[0]))


@with_mt5
def update_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    body = request.get_json()
    if not body:
        return jsonify({"error": "Request body required"}), 400

    # Checked before the order is looked up, so a bad value never costs an SDK call.
    stoplimit = None
    if "stoplimit" in body:
        stoplimit, err = parse_order_stoplimit(body["stoplimit"])
        if err:
            return jsonify({"error": err}), 400
    expiration = None
    if "expiration" in body:
        expiration, err = parse_order_expiration(body["expiration"])
        if err:
            return jsonify({"error": err}), 400

    orders = m(mt5.orders_get, ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404
    order = orders[0]

    # Fields the caller left out go back with the order's current values. The
    # order's own price_stoplimit and time_expiration are already broker time;
    # only a caller's `expiration` needs converting (parse_order_expiration).
    req = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "symbol": order.symbol,
        "price": float(body.get("price", order.price_open)),
        "stoplimit": order.price_stoplimit if stoplimit is None else stoplimit,
        "sl": float(body.get("sl", order.sl)),
        "tp": float(body.get("tp", order.tp)),
        "type_time": order.type_time,
        "expiration": order.time_expiration if expiration is None else expiration,
    }

    if "type_time" in body:
        tt = body["type_time"].upper()
        if tt in TIME_MAP:
            req["type_time"] = TIME_MAP[tt]
    elif expiration is not None and order.type_time not in SPECIFIED_TIME_TYPES:
        # A new expiry on an order that has none would be ignored by the broker.
        req["type_time"] = mt5.ORDER_TIME_SPECIFIED

    if req["type_time"] in SPECIFIED_TIME_TYPES and not req["expiration"]:
        return jsonify({"error": EXPIRATION_REQUIRED_ERROR}), 400

    result = m(mt5.order_send, **req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(m(mt5.last_error))
        return jsonify({"error": f"Failed to modify order: {err}"}), 500
    return jsonify(to_dict(result))


@with_mt5
def cancel_order(ticket):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    orders = m(mt5.orders_get, ticket=ticket)
    if not orders:
        return jsonify({"error": f"Order {ticket} not found"}), 404

    req = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": ticket,
    }

    result = m(mt5.order_send, **req)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = result.comment if result else str(m(mt5.last_error))
        return jsonify({"error": f"Failed to cancel order: {err}"}), 500
    return jsonify(to_dict(result))
