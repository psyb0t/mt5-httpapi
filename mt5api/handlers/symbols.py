import json
from datetime import datetime, timezone
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import jsonify, request

import MetaTrader5 as mt5

from mt5api import symbol_cache
from mt5api.config import (
    MODE,
    SYMBOL_IMPORT_MAX_BODY_BYTES,
    SYMBOL_IMPORT_MAX_SYMBOL_LENGTH,
    SYMBOL_IMPORT_MAX_SYMBOLS,
    TERMINAL_DIR,
    TIMEFRAME_MAP,
    TIMEFRAME_SECONDS,
    WICKWORKS_TIMEOUT_SECONDS,
    WICKWORKS_URL,
)
from mt5api.jsonkeys import with_legacy_keys
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
# 13 attempts: the final window is floor * 2^12 = 245760s, about 2.8 days,
# so a backward read crosses a weekend close instead of coming back empty.
# The loop stops at the first window that holds enough ticks, so a liquid
# symbol never reaches the wide windows.
TICKS_BACKWARD_MAX_ATTEMPTS = 13

# Bars or ticks returned when the query carries no `count`.
DEFAULT_COUNT = 100

_BAD_RANGE_ANCHOR = "'from'/'to' must be unix seconds or YYYY_MM_DD[_HH_MM_SS]"
_BAD_COUNT = "'count' must be an integer"


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


def _parse_count(count_arg):
    """Parse the signed `count` query value. Returns (count, error).

    An absent or empty value is DEFAULT_COUNT. Anything that is not an integer
    comes back as an error string for the caller to answer with 400; int()
    used to raise out of the handler and surface as a 500.
    """
    if not count_arg:
        return DEFAULT_COUNT, None
    try:
        return int(count_arg), None
    except ValueError:
        return None, _BAD_COUNT


def _now_utc_seconds():
    return int(datetime.now(timezone.utc).timestamp())


def list_symbols():
    """Refuse on a backtest terminal, otherwise serve the live SDK listing.

    Deliberately NOT @with_mt5: that decorator enters session(), which blocks
    on the global MT5 lock for up to SESSION_ACQUIRE_TIMEOUT before the
    handler body runs at all. A backtest terminal sitting behind a stuck SDK
    request would then answer this refusal with a 503 a minute later instead
    of an immediate 409 — the refusal needs no SDK, so it must not queue for
    one. The live path keeps the lock, via _list_symbols_live below.
    """
    # mode: backtest never attaches the SDK — ensure_initialized() finding no
    # terminal_info() would fall through to a full mt5.initialize(), which
    # spawns terminal64.exe and holds the tester's single-instance lock. That
    # used to be the documented way to prime this terminal's symbol cache;
    # instead it wedged the terminal, so it is refused before any SDK call.
    # Use POST /symbols/import to prime the cache without touching MT5.
    if MODE == "backtest":
        return jsonify({
            "error": (
                "GET /symbols is unsafe on a mode:backtest terminal: it "
                "would initialize the MT5 SDK and hold the tester's "
                "single-instance lock, leaving the next backtest run with "
                "an empty report. Use POST /symbols/import to prime the "
                "cache without touching MT5."
            ),
        }), 409
    return _list_symbols_live()


@with_mt5
def _list_symbols_live():
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503
    group = request.args.get("group")
    syms = m(mt5.symbols_get, group=group) if group else m(mt5.symbols_get)
    names = [s.name for s in syms] if syms else []
    # Persist only the UNFILTERED list: the backtest INI builder uses this to
    # decide a symbol does not exist, and a group-filtered subset would make it
    # draw that conclusion about every symbol the filter excluded.
    if not group and names:
        symbol_cache.save(TERMINAL_DIR, names, complete=True)
    return jsonify(names)


def import_symbols():
    """Prime this terminal's symbol cache without touching the MT5 SDK.

    Deliberately NOT @with_mt5 and calls no mt5.* function: this is the safe
    priming path for a mode:backtest terminal, which must never need an SDK
    request (see list_symbols above). Feed it a symbol list sourced elsewhere
    — GET /symbols against a live terminal on the same broker/account, or the
    broker's own symbol documentation.

    The list is merged into the existing cache. Only ``"complete": true``,
    which says the list is the broker's full book, replaces the cache and
    lets the INI builder treat a missing suffixed name as absent.

    Bounded on three axes, all configurable (see mt5api/config.py) and all
    checked before the resource they bound is spent: the raw body, the number
    of entries, and the length of each normalized name.
    """
    # ── Body size, BEFORE request.get_json() ──────────────────────────
    # get_json() pulls the entire body into memory and builds a Python object
    # graph from it, so a check placed after it has already paid the cost the
    # cap exists to prevent. Content-Length is the only thing available before
    # a single byte is parsed, so that is what this gate reads.
    declared = request.content_length
    if declared is None and request.headers.get("Transfer-Encoding"):
        # No declared length AND a transfer encoding: a streamed body whose
        # size is unknowable until it has all been read, so there is nothing
        # for this gate to check. Deliberately refused rather than waved
        # through — waving it through is precisely the hole the cap closes.
        # This endpoint's body is one small JSON object and every real client
        # (curl -d, requests, the unifier's `request` tool) sends it with a
        # Content-Length; 411 is the code RFC 9110 defines for exactly this.
        # (content_length is also None for a request with no body at all,
        # which carries no Transfer-Encoding and needs no bounding — it falls
        # through to the same 400 an empty body has always produced.)
        return jsonify({
            "error": (
                "request must declare a Content-Length; chunked bodies are "
                "not accepted on this endpoint"
            ),
        }), 411
    if declared is not None and declared > SYMBOL_IMPORT_MAX_BODY_BYTES:
        log.warning(
            "symbols/import: rejected %d-byte body (cap %d)",
            declared, SYMBOL_IMPORT_MAX_BODY_BYTES,
        )
        return jsonify({
            "error": (
                f"request body is {declared} bytes; this server accepts at "
                f"most {SYMBOL_IMPORT_MAX_BODY_BYTES} "
                "(SYMBOL_IMPORT_MAX_BODY_BYTES)"
            ),
        }), 413

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        # get_json(silent=True) happily returns a bare list/number/string for
        # syntactically valid JSON that isn't an object — body.get() below
        # would then raise AttributeError instead of a clean 400.
        return jsonify({"error": "request body must be a JSON object with a 'symbols' array"}), 400
    names = body.get("symbols")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return jsonify({"error": "request body must include a 'symbols' array of strings"}), 400
    complete = body.get("complete", False)
    if not isinstance(complete, bool):
        return jsonify({"error": "'complete' must be true or false"}), 400
    # Counted on the raw array, not the deduplicated result: this bounds the
    # strip/dedupe/sort work below, which happens before any dedupe can shrink
    # the list, and "items in 'symbols'" is what the caller actually sent.
    if len(names) > SYMBOL_IMPORT_MAX_SYMBOLS:
        log.warning(
            "symbols/import: rejected %d symbols (cap %d)",
            len(names), SYMBOL_IMPORT_MAX_SYMBOLS,
        )
        return jsonify({
            "error": (
                f"'symbols' has {len(names)} entries; this server accepts at "
                f"most {SYMBOL_IMPORT_MAX_SYMBOLS} (SYMBOL_IMPORT_MAX_SYMBOLS)"
            ),
        }), 400
    # This is operator/copy-paste input, unlike the SDK-sourced names
    # GET /symbols persists — strip stray whitespace so it cannot silently
    # defeat the exact-match lookup in backtest.handler._normalize_symbol.
    cleaned = sorted({n.strip() for n in names if n.strip()})
    if not cleaned:
        return jsonify({"error": "'symbols' must contain at least one non-empty name"}), 400
    # Measured on the NORMALIZED name — the stripped form is what lands in the
    # cache and what the INI builder matches against, so padding a legal name
    # with whitespace must not fail, and padding an illegal one must not pass.
    too_long = [n for n in cleaned if len(n) > SYMBOL_IMPORT_MAX_SYMBOL_LENGTH]
    if too_long:
        log.warning(
            "symbols/import: rejected %d oversized symbol name(s) (cap %d)",
            len(too_long), SYMBOL_IMPORT_MAX_SYMBOL_LENGTH,
        )
        return jsonify({
            "error": (
                f"{len(too_long)} symbol name(s) exceed "
                f"{SYMBOL_IMPORT_MAX_SYMBOL_LENGTH} characters "
                "(SYMBOL_IMPORT_MAX_SYMBOL_LENGTH); longest is "
                f"{max(len(n) for n in too_long)}"
            ),
        }), 400
    saved, total, is_complete = symbol_cache.merge(TERMINAL_DIR, cleaned, complete)
    if not saved:
        return jsonify({"error": "failed to write symbol cache"}), 500
    return jsonify({"imported": len(cleaned), "cached": total, "complete": is_complete})


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
                jsonify({"error": _BAD_RANGE_ANCHOR}),
                400,
            )
        df = utc_seconds_to_broker_dt(from_utc)
        dt = utc_seconds_to_broker_dt(to_utc)
        rates = m(mt5.copy_rates_range, symbol, timeframe, df, dt)
        return (list(rates) if rates is not None else []), tf_str, None

    count, count_err = _parse_count(count_arg)
    if count_err is not None:
        return None, "", (jsonify({"error": count_err}), 400)
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
    """Convert MT5 bars to Wickworks v0.7 canonical OHLCV bars."""
    return [{
        "time": broker_to_utc_seconds(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
        "volume": int(r[5]),
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
    ta_body, status, conn_err = _call_wickworks(wickworks_payload)
    if conn_err is not None:
        log.warning("wickworks call failed: %s", conn_err)
        return jsonify({"error": conn_err}), 502
    if status >= 400:
        return jsonify(with_legacy_keys({
            "error": "wickworks rejected request",
            "wickworksStatus": status,
            "wickworksBody": ta_body,
        })), 502

    return jsonify({
        "symbol": symbol,
        "timeframe": tf_str,
        "bars": bars_out,
        "ta": ta_body,
    })


def _ticks_backward(symbol, anchor_utc, abs_count, flags):
    """The abs_count ticks ending at anchor_utc (inclusive), oldest first.

    copy_ticks_from natively goes FORWARD and the SDK has no backward-by-count
    call, so this fakes it with a range query, starting tight (~0.1s per tick
    floor) and doubling the window on shortfall. A quiet symbol or a closed
    market can leave fewer than abs_count ticks inside the last window; those
    are returned as they are, or [] when there are none.
    """
    df = utc_seconds_to_broker_dt(anchor_utc)
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
            return []
        ticks = [t for t in got
                 if broker_to_utc_seconds(t[0]) <= anchor_utc]
        if len(ticks) >= abs_count:
            return ticks[-abs_count:]
        window *= 2
    return ticks


@with_mt5
def get_ticks(symbol):
    if not ensure_initialized():
        return jsonify({"error": "MT5 not initialized"}), 503

    if not ensure_symbol(symbol):
        return jsonify({"error": f"Symbol {symbol} not found"}), 404

    flags_str = request.args.get("flags", "ALL").upper()
    flags = TICK_FLAGS_MAP.get(flags_str)
    if flags is None:
        valid_flags = list(TICK_FLAGS_MAP.keys())
        return jsonify({"error": f"Invalid flags: {flags_str}. Use: {valid_flags}"}), 400

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
            return jsonify({"error": _BAD_RANGE_ANCHOR}), 400
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

    count, count_err = _parse_count(count_arg)
    if count_err is not None:
        return jsonify({"error": count_err}), 400

    # with `from`: count > 0 is N ticks forward from it (inclusive), count < 0
    # is |N| ticks backward ending at it (inclusive).
    # without `from`: the latest |N| ticks up to now, whatever the sign.
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
            ticks = _ticks_backward(symbol, anchor_utc, abs_count, flags)
    else:
        # No `from`: the latest ticks up to now. copy_ticks_from reads forward
        # from its start date, so a fixed early date would give the oldest
        # ticks the terminal holds instead.
        ticks = _ticks_backward(symbol, _now_utc_seconds(), abs_count, flags)

    if ticks is None or len(ticks) == 0:
        return jsonify([])

    return jsonify([{
        "time": broker_to_utc_seconds(t[0]),
        "bid": float(t[1]), "ask": float(t[2]),
        "last": float(t[3]), "volume": int(t[4]),
        "time_msc": broker_to_utc_ms(t[5]),
        "flags": int(t[6]), "volume_real": float(t[7]),
    } for t in ticks])
