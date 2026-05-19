import signal
import sys
import threading
import time

from waitress import serve

from mt5api.backtest import jobs as backtest_jobs
from mt5api.config import ACCOUNT, BROKER, HOST, INSTANCE, MODE, PORT
from mt5api.logger import log
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

    log.info(
        "HTTP API listening on %s:%d (waitress, threads=%d, conn_limit=%d, max_queue_depth=%d)",
        HOST, PORT, WSGI_THREADS, WSGI_CONNECTION_LIMIT, MAX_QUEUE_DEPTH,
    )
    try:
        serve(
            app,
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
