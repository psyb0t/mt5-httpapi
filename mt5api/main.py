import asyncio
import signal
import sys
import threading
import time

from a2wsgi import ASGIMiddleware
from waitress import serve
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from mt5api.backtest import jobs as backtest_jobs
from mt5api.config import ACCOUNT, API_TOKEN, BROKER, HOST, INSTANCE, MODE, PORT
from mt5api.logger import log
from mt5api.mcp_server import build_mcp_server
from mt5api.monitor import start_monitor
from mt5api.mt5client import (
    MAX_QUEUE_DEPTH,
    ensure_initialized,
    init_mt5,
    session,
)
from mt5api.server import app

# Each handler that touches MT5 grabs a process-wide mutex for its full
# duration (see @with_mt5 in mt5client.py), so only one MT5 worker runs at
# a time regardless of waitress threads. Extra threads are cheap and let
# /ping (lock-free) plus queued requests cross the auth/log fast path
# without being serialized behind a slow MT5 call. Keep this comfortably
# above MAX_QUEUE_DEPTH so the queue itself can grow before threads run
# out — accepted-but-thread-blocked is much faster to fast-503 than
# accepted-and-stuck-on-the-mt5-lock.
WSGI_THREADS = 32
# Cap concurrent TCP connections so a stuck terminal can't blow up file
# descriptors. Excess clients get refused at accept().
WSGI_CONNECTION_LIMIT = 100
# Drop idle clients after this many seconds.
WSGI_CHANNEL_TIMEOUT = 60

RETRY_INTERVAL = 30

# Seconds to let the terminal GUI settle before re-applying the WebRequest
# allowlist via AutoIt on boot (see _reapply_webrequest_once).
WEBREQUEST_BOOT_DELAY = 25
_webrequest_reapplied = threading.Event()


def _reapply_webrequest_once():
    """Re-apply this terminal's desired WebRequest allowlist via AutoIt after a
    (re)start. MT5 on the VM drops the list every restart, so the API re-sets it
    once the terminal GUI is up. No-op unless AutoIt is present (the VM) AND a
    desired allowlist has been configured. Runs in a daemon thread so it never
    blocks startup; guarded to run only once per process."""
    if _webrequest_reapplied.is_set():
        return
    _webrequest_reapplied.set()

    def _work():
        try:
            from mt5api.chartctl import autoit_webrequest as autoit
            from mt5api.chartctl import webrequest as wr
            from mt5api.config import TERMINAL_DIR

            if not autoit.available():
                return
            urls = wr.effective_urls(wr.config_dir(TERMINAL_DIR))
            if not urls:
                return
            # Let charts/loader finish attaching, plus a per-terminal stagger
            # (from the port) so multiple terminals on one host don't all pile
            # onto the machine-wide GUI mutex at once.
            time.sleep(WEBREQUEST_BOOT_DELAY + (PORT % 10) * 3)
            for attempt in range(1, 4):        # brief retry: GUI may still be busy
                status, _log = autoit.apply_urls(urls)
                log.info(
                    "Boot WebRequest re-apply attempt %d: %d url(s) -> %s",
                    attempt, len(urls), status,
                )
                if status == "OK":
                    break
                time.sleep(15)
        except Exception:
            log.exception("Boot WebRequest re-apply failed")

    threading.Thread(target=_work, daemon=True).start()


# --- MCP interface -----------------------------------------------------------
# TODO: change to fastapi or smth. This WSGI<->ASGI bridge is a stopgap. mt5api
# is a Flask/WSGI app (served by waitress), but the MCP server is an ASGI
# (Starlette) app. a2wsgi.ASGIMiddleware adapts it to WSGI so waitress can serve
# it, and werkzeug's DispatcherMiddleware routes /mcp to it while every other
# path stays on Flask. Migrating mt5api to FastAPI/ASGI would let the MCP app
# mount natively (the way ibkr-httpapi does) and delete this bridge + auth shim.
_MCP_SERVER = build_mcp_server()
_MCP_BRIDGE = ASGIMiddleware(_MCP_SERVER.streamable_http_app())

# a2wsgi runs the ASGI app on a background loop but does NOT drive the ASGI
# lifespan, so the streamable-HTTP session manager has to be started by hand
# (from main(), before serving). This is how long we wait for it to come up.
MCP_SESSION_START_TIMEOUT = 10


def _start_mcp_session_manager():
    """Start FastMCP's streamable-HTTP session manager on a2wsgi's request loop.

    a2wsgi.ASGIMiddleware runs the ASGI app on its own background event loop but
    does NOT run the ASGI lifespan, so the StreamableHTTPSessionManager task
    group would never start and every /mcp request would fail with "Task group
    is not initialized". Start it on that SAME loop (so the task group lives
    where requests are handled) and block until it is ready. On failure we log
    and continue: a broken MCP bridge must not take the REST trading API down."""
    ready = threading.Event()

    async def _run():
        async with _MCP_SERVER.session_manager.run():
            ready.set()
            await asyncio.Event().wait()

    asyncio.run_coroutine_threadsafe(_run(), _MCP_BRIDGE.loop)
    if not ready.wait(timeout=MCP_SESSION_START_TIMEOUT):
        log.error(
            "MCP session manager did not start within %ds — /mcp will be "
            "unavailable, but the REST API continues.",
            MCP_SESSION_START_TIMEOUT,
        )


def _mcp_auth_gate(mcp_wsgi_app):
    """Re-apply the API bearer check in front of the /mcp app.

    DispatcherMiddleware routes /mcp AROUND Flask, so Flask's ``before_request``
    auth (server.py) never runs for it. Without this shim /mcp would be an
    unauthenticated door into the trading API. Mirrors the app's own check
    exactly (empty API_TOKEN = auth disabled, same as the REST side)."""

    def _gated(environ, start_response):
        if API_TOKEN and environ.get("HTTP_AUTHORIZATION", "") != f"Bearer {API_TOKEN}":
            start_response(
                "401 Unauthorized",
                [("Content-Type", "application/json")],
            )
            return [b'{"error": "unauthorized"}']
        return mcp_wsgi_app(environ, start_response)

    return _gated


_WSGI_APP = DispatcherMiddleware(app, {"/mcp": _mcp_auth_gate(_MCP_BRIDGE)})


def _background_init():
    """Keep retrying MT5 init until it connects. Runs in a daemon thread.

    Acquires the MT5 session lock per attempt so retries serialize cleanly
    against handler / monitor calls (the SDK is single-process,
    single-connection).
    """
    attempt = 0
    while True:
        attempt += 1
        log.info("MT5 init attempt %d...", attempt)
        try:
            with session():
                connected = ensure_initialized()
        except Exception as e:
            log.warning("MT5 init session failed: %s", e)
            connected = False
        if connected:
            log.info("MT5 connected on attempt %d.", attempt)
            _reapply_webrequest_once()
            return
        log.warning("MT5 not ready, retrying in %ds...", RETRY_INTERVAL)
        time.sleep(RETRY_INTERVAL)


def _handle_signal(sig, _frame):
    log.critical("Received signal %d — exiting.", sig)
    sys.exit(sig)


def main():
    log.info(
        "Starting — broker=%s account=%s instance=%s port=%d mode=%s",
        BROKER,
        ACCOUNT,
        INSTANCE,
        PORT,
        MODE,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if MODE == "backtest":
        log.info(
            "mode=backtest — skipping MT5 SDK init and live health monitor "
            "so terminal64.exe /portable can be spawned by the tester "
            "without hitting MT5's single-instance lock on this data dir."
        )
    else:
        try:
            with session():
                connected = init_mt5()
        except Exception as e:
            log.warning("Startup init session failed: %s", e)
            connected = False

        if connected:
            log.info("MT5 connected.")
            _reapply_webrequest_once()
        else:
            log.warning(
                "MT5 not ready yet, retrying every %ds in background...",
                RETRY_INTERVAL,
            )
            t = threading.Thread(target=_background_init, daemon=True)
            t.start()

        start_monitor()

    swept = backtest_jobs.sweep_orphans()
    if swept:
        log.warning("Backtest sweep marked %d orphaned job(s) as failed.", swept)

    _start_mcp_session_manager()

    log.info(
        "HTTP API listening on %s:%d (waitress, threads=%d, conn_limit=%d, max_queue_depth=%d)",
        HOST, PORT, WSGI_THREADS, WSGI_CONNECTION_LIMIT, MAX_QUEUE_DEPTH,
    )
    try:
        serve(
            _WSGI_APP,
            host=HOST,
            port=PORT,
            threads=WSGI_THREADS,
            connection_limit=WSGI_CONNECTION_LIMIT,
            channel_timeout=WSGI_CHANNEL_TIMEOUT,
            ident="mt5-httpapi",
        )
    except Exception:
        log.critical("WSGI server crashed.", exc_info=True)
        raise
    finally:
        log.critical(
            "API process exiting — broker=%s account=%s instance=%s port=%d",
            BROKER,
            ACCOUNT,
            INSTANCE,
            PORT,
        )


if __name__ == "__main__":
    main()
