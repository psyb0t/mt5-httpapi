import os
import time

from flask import Flask, g, jsonify, request
from flask_compress import Compress
from mt5api.backtest import handler as backtest_handler
from mt5api.config import (
    API_TOKEN,
    CHARTCTL_ENABLED,
    COMPILE_API_TOKEN,
    FILES_ENABLED,
    MAX_REQUEST_BODY_BYTES,
    MAX_UPLOAD_BODY_BYTES,
)
from mt5api.handlers import (
    account,
    compile as compile_handler,
    history,
    orders,
    positions,
    symbols,
    terminal,
)
from mt5api import reboot_guard
from mt5api.logger import log

app = Flask(__name__)

#: The 401 body for a missing or wrong bearer token, on REST and /mcp alike.
UNAUTHORIZED_BODY = {"error": "unauthorized", "code": "UNAUTHORIZED"}


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "-"


#: Paths that write no request log lines. The container healthcheck probes
#: every port on the VM at the same moment, every 30 s, and each log line takes
#: full.log's cross-process lock on the Shared folder. Logging those probes
#: queued ~90 writes on one lock per check: an idle /ping took 1.1 s on a
#: 45-terminal VM and 5-10 s under load, which the healthcheck counts as a hung
#: port, and the watchdog then recreated a VM that was only busy.
UNLOGGED_PATHS = frozenset({"/ping"})


@app.before_request
def _start_request():
    g.req_start = time.monotonic()
    g.req_id = os.urandom(4).hex()
    g.req_logged = request.path not in UNLOGGED_PATHS
    if g.req_logged:
        log.info(
            "%s -> %s %s ip=%s ua=%r",
            g.req_id, request.method, request.full_path,
            _client_ip(), request.headers.get("User-Agent", "-"),
        )
    refusal = _authorize()
    if refusal is not None:
        return refusal
    return _guard_write()


def _guard_write():
    """Hold a scheduled reboot while this request changes something.

    Runs only for an authorized request, so a caller without the token can
    neither hold a reboot nor learn that one is pending.
    """
    if not reboot_guard.is_write(request.method):
        return None
    if reboot_guard.draining():
        log.warning(
            "%s refused %s %s: a scheduled reboot is about to happen (reason=reboot_pending)",
            g.req_id, request.method, request.path,
        )
        response = jsonify({
            "error": "a scheduled VM reboot is about to happen; retry after it",
            "code": "REBOOT_PENDING",
        })
        response.headers["Retry-After"] = str(reboot_guard.DRAIN_RETRY_AFTER_SECONDS)
        return response, 503
    reboot_guard.write_started(g.req_id, request.method, request.path)
    return None


@app.teardown_request
def _end_write(_exc):
    req_id = getattr(g, "req_id", None)
    if req_id is not None:
        reboot_guard.write_finished(req_id)


def _authorize():
    """Refuse a request without the right token or with an oversized body."""
    auth = request.headers.get("Authorization", "")

    # /compile carries a SECOND, compile-only credential.
    #
    # A caller that only needs to compile must not hold a token that can also
    # place orders, close positions or restart a terminal. So API_TOKEN
    # is accepted everywhere including here, while COMPILE_API_TOKEN is accepted
    # ONLY on this path - every other route falls through to the original check
    # below, which compares against API_TOKEN alone and therefore rejects it.
    if request.path == "/compile":
        accepted = [f"Bearer {t}" for t in (API_TOKEN, COMPILE_API_TOKEN) if t]
        if accepted and auth not in accepted:
            # JSON rather than Flask's HTML error page: the client is documented
            # to treat a non-JSON body as a broken host.
            return jsonify({"ok": False, "log": "unauthorized"}), 401
        # Not _refuse_oversized_body(): /compile bounds its own body from
        # COMPILE_MAX_SOURCE_BYTES (411/413 before parsing, in the handler), in
        # the {ok, log} shape its callers parse. The generic cap would refuse a
        # legal source that JSON escaping took past MAX_REQUEST_BODY_BYTES.
        return None

    if API_TOKEN and auth != f"Bearer {API_TOKEN}":
        # JSON like every other error here: clients treat a non-JSON body as
        # a broken host rather than a bad token.
        return jsonify(UNAUTHORIZED_BODY), 401
    return _refuse_oversized_body()


def _refuse_oversized_body():
    """Bound the request body before any handler parses it.

    Deliberately here and not in each handler. A per-endpoint cap has to be
    remembered on every route added afterwards, and the one that gets forgotten
    is the hole — which is how POST /symbols/import came to accept a 2 MB
    symbol name while six other JSON routes had no bound at all. An endpoint
    that needs a tighter cap still declares one and it fires first, because it
    is checked inside the handler; this only catches what nothing else bounded.

    Content-Length is what makes this a PRE-PARSE gate: it is the one thing
    known before a byte is read. A body with no declared length is left to the
    endpoint (POST /symbols/import refuses it outright) and, in production, to
    waitress, which de-chunks and supplies a length before Flask sees the
    request at all.

    Runs after the auth check so an unauthenticated caller cannot probe the
    limits, and answers JSON because the client treats a non-JSON body as a
    broken host.
    """
    declared = request.content_length
    if declared is None:
        return None
    multipart = (request.mimetype or "").startswith("multipart/")
    is_upload = multipart or _is_file_upload()
    limit = MAX_UPLOAD_BODY_BYTES if is_upload else MAX_REQUEST_BODY_BYTES
    if declared <= limit:
        return None
    setting = "MAX_UPLOAD_BODY_BYTES" if is_upload else "MAX_REQUEST_BODY_BYTES"
    log.warning(
        "%s rejected %d-byte body on %s %s (cap %d)",
        getattr(g, "req_id", "--------"), declared,
        request.method, request.path, limit,
    )
    return jsonify({
        "error": (
            f"request body is {declared} bytes; this server accepts at most "
            f"{limit} ({setting})"
        ),
    }), 413


_FILE_UPLOAD_PREFIXES = ("/files/", "/compile/files/")


def _is_file_upload():
    """A PUT to the file API, whose raw body is a file, not JSON."""
    return request.method == "PUT" and request.path.startswith(_FILE_UPLOAD_PREFIXES)


@app.after_request
def _end_request(response):
    if not getattr(g, "req_logged", True):
        return response
    start = getattr(g, "req_start", None)
    elapsed_ms = (time.monotonic() - start) * 1000 if start else -1
    req_id = getattr(g, "req_id", "--------")
    size = response.calculate_content_length()
    if size is None:
        size = response.headers.get("Content-Length", "-")
    log.info(
        "%s <- %s %s status=%s bytes=%s dur_ms=%.1f",
        req_id, request.method, request.full_path,
        response.status_code, size, elapsed_ms,
    )
    return response


# Compress registered AFTER our after_request so its hook runs first
# (Flask invokes after_request hooks in reverse registration order),
# letting us log post-compression Content-Length.
Compress(app)


# ── Health / System ──────────────────────────────────────────────
app.get("/ping")(terminal.ping)
app.get("/error")(terminal.last_error)


@app.get("/busy")
def busy():
    """Whether a VM reboot now would break work here, and why. Polled by
    scripts/reboot_guard.py before every scheduled reboot."""
    return jsonify(reboot_guard.status())

# ── Terminal ─────────────────────────────────────────────────────
app.get("/terminal")(terminal.get_terminal)
app.post("/terminal/init")(terminal.init)
app.post("/terminal/shutdown")(terminal.shutdown)
app.post("/terminal/restart")(terminal.restart)

# ── Account ──────────────────────────────────────────────────────
app.get("/account")(account.get_account)

# ── Symbols ──────────────────────────────────────────────────────
app.get("/symbols")(symbols.list_symbols)
app.post("/symbols/import")(symbols.import_symbols)
app.get("/symbols/<symbol>")(symbols.get_symbol)
app.get("/symbols/<symbol>/tick")(symbols.get_tick)
app.get("/symbols/<symbol>/rates")(symbols.get_rates)
app.post("/symbols/<symbol>/rates/ta")(symbols.get_rates_ta)
app.get("/symbols/<symbol>/ticks")(symbols.get_ticks)

# ── Positions ────────────────────────────────────────────────────
app.get("/positions")(positions.list_positions)
app.get("/positions/<int:ticket>")(positions.get_position)
app.put("/positions/<int:ticket>")(positions.update_position)
app.delete("/positions/<int:ticket>")(positions.close_position)

# ── Orders ───────────────────────────────────────────────────────
app.get("/orders")(orders.list_orders)
app.post("/orders")(orders.create_order)
app.get("/orders/<int:ticket>")(orders.get_order)
app.put("/orders/<int:ticket>")(orders.update_order)
app.delete("/orders/<int:ticket>")(orders.cancel_order)

# ── History ──────────────────────────────────────────────────────
app.get("/history/orders")(history.get_orders)
app.get("/history/deals")(history.get_deals)

# ── Compile ──────────────────────────────────────────────────────
# Source text in, .ex5 out. Auth for this one route is handled in
# _start_request above; it accepts COMPILE_API_TOKEN as well as API_TOKEN.
app.post("/compile")(compile_handler.compile_source)

# ── Chart Deployments (chartctl) ─────────────────────────────────
# Lock-free EA deployment primitives plus the WebRequest allowlist. Gated:
# live mode + config enabled.
if CHARTCTL_ENABLED:
    from mt5api.handlers.chartctl_routes import register_chartctl_routes

    register_chartctl_routes(app)

# ── File API ─────────────────────────────────────────────────────
# Read, write, unzip and delete files in the terminal's install directory and
# the compile tree. Gated: config files.enabled (opt-in).
if FILES_ENABLED:
    from mt5api.handlers.files import register_files_routes

    register_files_routes(app)

# ── Backtest ─────────────────────────────────────────────────────
app.post("/backtest/build-ini")(backtest_handler.build_ini_route)
app.post("/backtest/build-set")(backtest_handler.build_set_route)
app.post("/backtest")(backtest_handler.run_backtest)
app.get("/backtest/<job_id>")(backtest_handler.get_status)
app.get("/backtest/<job_id>/report")(backtest_handler.get_report)
app.get("/backtest/<job_id>/log")(backtest_handler.get_log)
app.get("/backtest/<job_id>/tail")(backtest_handler.get_tail)
app.post("/backtest/<job_id>/cancel")(backtest_handler.cancel_backtest)
