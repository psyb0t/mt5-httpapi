import json
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api.config import (
    TIMEFRAME_MAP,
    TIMEFRAME_SECONDS,
    WICKWORKS_TIMEOUT_SECONDS,
    WICKWORKS_URL,
)
from mt5api.logger import log
from mt5api.mt5client import (
    broker_to_utc_ms,
    broker_to_utc_seconds,
    ensure_initialized,
    ensure_symbol,
    m,
    to_dict,
    utc_seconds_to_broker_dt,
    with_mt5,
)

TICK_FLAGS_MAP = {
    "ALL": mt5.COPY_TICKS_ALL,
    "INFO": mt5.COPY_TICKS_INFO,
    "TRADE": mt5.COPY_TICKS_TRADE,
}

# Retry-doubling budgets. The MT5 SDK has no native "N forward bars from
# date" or "N backward ticks from date" call, so both branches fake it
# with copy_*_range over a guessed window. Start tight, double on
# shortfall, cap attempts so a pathological symbol doesn't loop forever.
RATES_FORWARD_INITIAL_MULT = 1.5  # covers weekend/holiday gaps on intraday
RATES_FORWARD_MAX_ATTEMPTS = 4    # final window: 1.5 * 2^3 = 12x
TICKS_BACKWARD_INITIAL_SEC_PER_TICK = 0.1  # ~10 ticks/sec assumption
TICKS_BACKWARD_FLOOR_WINDOW_SEC = 60       # min window for sparse symbols
TICKS_BACKWARD_MAX_ATTEMPTS = 6   # final window: floor * 2^5


def _parse_anchor(s):
    """Parse a `from`/`to` query value into real-UTC unix seconds.

    Accepted forms:
      - bare integer seconds (may be negative)
      - 'YYYY_MM_DD_HH_MM_SS' — explicit datetime, interpreted as real UTC
      - 'YYYY_MM_DD' — date only, time defaults to 00:00:00 UTC

    Returns int unix seconds, or None on parse failure.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # Bare integer (no underscores — Python's int() accepts '2024_01_15' as
    # a numeric literal with digit separators, which would mis-parse our
    # date format).
    if "_" not in s:
        try:
            return int(s)
        except ValueError:
            pass
    parts = s.split("_")
    try:
        if len(parts) == 6:
            y, mo, d, h, mi, se = (int(p) for p in parts)
            dt = datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
        elif len(parts) == 3:
            y, mo, d = (int(p) for p in parts)
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
        else:
            return None
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


@with_mt5
def list_symbols():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    group = request.args.get("group")
    syms = m(mt5.symbols_get, group=group) if group else m(mt5.symbols_get)
    return jsonify([s.name for s in syms] if syms else [])


@with_mt5
def get_symbol(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    info = m(mt5.symbol_info, symbol)
    if info is None:
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    return jsonify(to_dict(info))


@with_mt5
def get_tick(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    if not ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404
    tick = m(mt5.symbol_info_tick, symbol)
    if tick is None:
        return jsonify({"error": f"No tick for {symbol}"}), 404
    return jsonify(to_dict(tick))


def _rates_to_dicts(rates):
    return [{
        "time": broker_to_utc_seconds(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5]),
        "spread": int(r[6]), "real_volume": int(r[7]),
    } for r in rates]


def _fetch_rates(symbol):
    """Resolve a rates query from request.args. Returns (rates, tf_str, err).

    On success: (rates_list_or_empty, tf_str, None) — rates may be an empty
    list when the query is well-formed but produced no bars.
    On error:   (None, "", (response, status)) — caller returns it as-is.

    MT5 must already be initialized; caller is responsible for the @with_mt5
    wrapper and the ensure_initialized() check.
    """
    tf_str = request.args.get("timeframe", "M1").upper()
    timeframe = TIMEFRAME_MAP.get(tf_str)
    if timeframe is None:
        return None, "", (
            jsonify({"error": f"Invalid timeframe: {tf_str}. Use: {list(TIMEFRAME_MAP.keys())}"}),
            400,
        )

    if not ensure_symbol(symbol):
        return None, "", (jsonify({"error": f"Symbol {symbol} not found"}), 404)

    date_from = request.args.get("from")
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    # Three modes: range (from+to), anchor+count (from+count or count alone),
    # or default (count=100 from now). count and to are mutually exclusive.
    if date_to is not None and count_arg is not None:
        return None, "", (jsonify({"error": "use either 'count' or 'to', not both"}), 400)
    if date_to is not None and date_from is None:
        return None, "", (jsonify({"error": "'to' requires 'from'"}), 400)

    if date_to is not None:
        from_utc = _parse_anchor(date_from)
        to_utc = _parse_anchor(date_to)
        if from_utc is None or to_utc is None:
            return None, "", (
                jsonify({"error": "'from'/'to' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}),
                400,
            )
        df = utc_seconds_to_broker_dt(from_utc)
        dt = utc_seconds_to_broker_dt(to_utc)
        rates = m(mt5.copy_rates_range, symbol, timeframe, df, dt)
        return (list(rates) if rates is not None else []), tf_str, None

    count = int(count_arg) if count_arg else 100
    if count == 0:
        return [], tf_str, None
    abs_count = abs(count)

    if date_from:
        anchor_utc = _parse_anchor(date_from)
        if anchor_utc is None:
            return None, "", (
                jsonify({"error": "'from' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}),
                400,
            )
        df = utc_seconds_to_broker_dt(anchor_utc)
        if count > 0:
            # Forward from anchor (inclusive): MT5's copy_rates_from goes
            # BACKWARD from a date. Fake forward-by-count with a windowed
            # range query, starting tight and doubling on shortfall.
            tf_secs = TIMEFRAME_SECONDS.get(tf_str, 60)
            mult = RATES_FORWARD_INITIAL_MULT
            rates = []
            for _ in range(RATES_FORWARD_MAX_ATTEMPTS):
                end_utc = anchor_utc + int(abs_count * tf_secs * mult) + tf_secs
                end_dt = utc_seconds_to_broker_dt(end_utc)
                got = m(mt5.copy_rates_range, symbol, timeframe, df, end_dt)
                if got is None:
                    rates = []
                    break
                rates = [r for r in got
                         if broker_to_utc_seconds(r[0]) >= anchor_utc]
                if len(rates) >= abs_count:
                    rates = rates[:abs_count]
                    break
                mult *= 2
        else:
            # Backward ending at anchor (inclusive): copy_rates_from natively
            # returns abs_count bars whose time is at-or-before anchor.
            rates = m(mt5.copy_rates_from, symbol, timeframe, df, abs_count)
    else:
        # No `from` — last N bars from current bar
        rates = m(mt5.copy_rates_from_pos, symbol, timeframe, 0, abs_count)

    return (list(rates) if rates is not None else []), tf_str, None


@with_mt5
def get_rates(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    rates, _, err = _fetch_rates(symbol)
    if err is not None:
        return err
    return jsonify(_rates_to_dicts(rates))


def _bars_to_wickworks(rates):
    """MT5 bar shape -> wickworks bar shape (camelCase volume fields)."""
    return [{
        "time": broker_to_utc_seconds(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
        "tickVolume": int(r[5]),
        "realVolume": int(r[7]),
    } for r in rates]


def _call_wickworks(payload):
    """POST to wickworks and return (parsed_body, status_code, err_str).

    Network/decode errors -> (None, 502, msg). HTTP errors from wickworks
    pass through the original status + parsed body so the caller can mirror
    them to the API consumer.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        WICKWORKS_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=WICKWORKS_TIMEOUT_SECONDS) as resp:
            body = resp.read()
            try:
                return json.loads(body.decode("utf-8")), resp.status, None
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return None, 502, f"wickworks returned non-JSON: {exc}"
    except urllib_error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"error": raw.decode("utf-8", errors="replace")[:500]}
        return parsed, exc.code, None
    except (urllib_error.URLError, OSError, TimeoutError) as exc:
        return None, 502, f"wickworks unreachable: {exc}"


@with_mt5
def get_rates_ta(symbol):
    """Same query params as GET /symbols/<symbol>/rates; body is a wickworks
    indicators spec. Returns the bars and the TA analysis as siblings.
    """
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    body = request.get_json(silent=True) or {}
    indicators = body.get("indicators")
    if not isinstance(indicators, dict) or not indicators:
        return jsonify({"error": "request body must include a non-empty 'indicators' object"}), 400

    rates, tf_str, err = _fetch_rates(symbol)
    if err is not None:
        return err

    bars_out = _rates_to_dicts(rates)
    if not rates:
        return jsonify({
            "symbol": symbol,
            "timeframe": tf_str,
            "bars": [],
            "ta": None,
        })

    wickworks_payload = {
        "symbol": symbol,
        "timeframe": tf_str,
        "bars": _bars_to_wickworks(rates),
        "indicators": indicators,
    }
    if "recentBars" in body:
        wickworks_payload["recentBars"] = body["recentBars"]

    ta_body, status, conn_err = _call_wickworks(wickworks_payload)
    if conn_err is not None:
        log.warning("wickworks call failed: %s", conn_err)
        return jsonify({"error": conn_err}), 502
    if status >= 400:
        return jsonify({
            "error": "wickworks rejected request",
            "wickworksStatus": status,
            "wickworksBody": ta_body,
        }), 502

    return jsonify({
        "symbol": symbol,
        "timeframe": tf_str,
        "bars": bars_out,
        "ta": ta_body,
    })


@with_mt5
def get_ticks(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    if not ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404

    flags_str = request.args.get("flags", "ALL").upper()
    flags = TICK_FLAGS_MAP.get(flags_str)
    if flags is None:
        return jsonify({"error": f"Invalid flags: {flags_str}. Use: {list(TICK_FLAGS_MAP.keys())}"}), 400

    date_from = request.args.get("from")
    date_to = request.args.get("to")
    count_arg = request.args.get("count")

    # Three modes: range (from+to), anchor+count (from+count or count alone),
    # or default (count=100 from now). count and to are mutually exclusive.
    if date_to is not None and count_arg is not None:
        return jsonify({"error": "use either 'count' or 'to', not both"}), 400
    if date_to is not None and date_from is None:
        return jsonify({"error": "'to' requires 'from'"}), 400

    if date_to is not None:
        from_utc = _parse_anchor(date_from)
        to_utc = _parse_anchor(date_to)
        if from_utc is None or to_utc is None:
            return jsonify({"error": "'from'/'to' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(from_utc)
        dt = utc_seconds_to_broker_dt(to_utc)
        ticks = m(mt5.copy_ticks_range, symbol, df, dt, flags)
        if ticks is None or len(ticks) == 0:
            return jsonify([])
        return jsonify([{
            "time": broker_to_utc_seconds(t[0]),
            "bid": float(t[1]), "ask": float(t[2]),
            "last": float(t[3]), "volume": int(t[4]),
            "time_msc": broker_to_utc_ms(t[5]),
            "flags": int(t[6]), "volume_real": float(t[7]),
        } for t in ticks])

    count = int(count_arg) if count_arg else 100

    # count > 0: N ticks forward from `from` (inclusive)
    # count < 0: |N| ticks backward ending at `from` (inclusive)
    # from omitted: anchor is now
    if count == 0:
        return jsonify([])

    abs_count = abs(count)

    if date_from:
        anchor_utc = _parse_anchor(date_from)
        if anchor_utc is None:
            return jsonify({"error": "'from' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"}), 400
        df = utc_seconds_to_broker_dt(anchor_utc)
        if count > 0:
            # Forward from anchor (inclusive). Unlike copy_rates_from,
            # copy_ticks_from natively goes FORWARD: returns abs_count ticks
            # whose time is at-or-after the anchor.
            ticks = m(mt5.copy_ticks_from, symbol, df, abs_count, flags)
        else:
            # Backward ending at anchor: copy_ticks_from natively goes
            # FORWARD, no native backward-by-count. Fake it with a range
            # query, starting tight (~0.1s per tick floor) and doubling
            # the window on shortfall.
            window = max(
                TICKS_BACKWARD_FLOOR_WINDOW_SEC,
                int(abs_count * TICKS_BACKWARD_INITIAL_SEC_PER_TICK),
            )
            ticks = []
            for _ in range(TICKS_BACKWARD_MAX_ATTEMPTS):
                start_utc = max(0, anchor_utc - window)
                df_start = utc_seconds_to_broker_dt(start_utc)
                got = m(mt5.copy_ticks_range, symbol, df_start, df, flags)
                if got is None:
                    ticks = []
                    break
                ticks = [t for t in got
                         if broker_to_utc_seconds(t[0]) <= anchor_utc]
                if len(ticks) >= abs_count:
                    ticks = ticks[-abs_count:]
                    break
                window *= 2
    else:
        ticks = m(
            mt5.copy_ticks_from,
            symbol, datetime(2000, 1, 1, tzinfo=timezone.utc),
            abs_count, flags,
        )

    if ticks is None or len(ticks) == 0:
        return jsonify([])

    return jsonify([{
        "time": broker_to_utc_seconds(t[0]),
        "bid": float(t[1]), "ask": float(t[2]),
        "last": float(t[3]), "volume": int(t[4]),
        "time_msc": broker_to_utc_ms(t[5]),
        "flags": int(t[6]), "volume_real": float(t[7]),
    } for t in ticks])
