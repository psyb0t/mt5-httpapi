import signal
import sys
import threading
import time

from mt5api.config import ACCOUNT, BROKER, HOST, PORT
from mt5api.logger import log
from mt5api.monitor import start_monitor
from mt5api.mt5client import ensure_initialized, init_mt5
from mt5api.server import app

RETRY_INTERVAL = 30


def _background_init():
    """Keep retrying MT5 init until it connects. Runs in a daemon thread."""
    attempt = 0
    while True:
        attempt += 1
        log.info("MT5 init attempt %d...", attempt)
        if ensure_initialized():
            log.info("MT5 connected on attempt %d.", attempt)
            return
        log.warning("MT5 not ready, retrying in %ds...", RETRY_INTERVAL)
        time.sleep(RETRY_INTERVAL)


def _handle_signal(sig, _frame):
    log.critical("Received signal %d — exiting.", sig)
    sys.exit(sig)


def main():
    log.info("Starting — broker=%s account=%s port=%d", BROKER, ACCOUNT, PORT)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    connected = init_mt5()
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

    log.info("HTTP API listening on %s:%d", HOST, PORT)
    try:
        app.run(host=HOST, port=PORT)
    except Exception:
        log.critical("Flask server crashed.", exc_info=True)
        raise
    finally:
        log.critical(
            "API process exiting — broker=%s account=%s port=%d",
            BROKER, ACCOUNT, PORT,
        )


if __name__ == "__main__":
    main()
