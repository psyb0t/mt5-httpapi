import threading
import time

from mt5api.config import ACCOUNT, BROKER, HOST, PORT
from mt5api.mt5client import ensure_initialized, init_mt5
from mt5api.server import app

RETRY_INTERVAL = 30


def _background_init():
    """Keep retrying MT5 init until it connects. Runs in a daemon thread."""
    attempt = 0
    while True:
        attempt += 1
        print(f"MT5 init attempt {attempt}...", flush=True)
        if ensure_initialized():
            print(f"MT5 connected on attempt {attempt}.", flush=True)
            return
        print(f"MT5 not ready, retrying in {RETRY_INTERVAL}s...", flush=True)
        time.sleep(RETRY_INTERVAL)


def main():
    print(f"Broker: {BROKER}, Account: {ACCOUNT}", flush=True)

    connected = init_mt5()
    if connected:
        print("MT5 connected.", flush=True)
    else:
        print(
            f"MT5 not ready yet, will keep retrying every {RETRY_INTERVAL}s in background...",
            flush=True,
        )
        t = threading.Thread(target=_background_init, daemon=True)
        t.start()

    print(f"\nMT5 HTTP API listening on {HOST}:{PORT}", flush=True)
    print(
        """
Endpoints:
  GET    /ping                          Health check
  GET    /error                         Last MT5 error

  GET    /terminal                      Terminal info
  POST   /terminal/init                 Initialize MT5
  POST   /terminal/shutdown             Shutdown MT5

  GET    /account                       Account info

  GET    /symbols                       List symbols (?group=*USD*)
  GET    /symbols/:symbol               Symbol details
  GET    /symbols/:symbol/tick          Latest tick
  GET    /symbols/:symbol/rates         OHLCV (?timeframe=H1&count=100)
  GET    /symbols/:symbol/ticks         Tick data (?count=100)

  GET    /positions                     List open positions (?symbol=)
  GET    /positions/:ticket             Get position
  PUT    /positions/:ticket             Update SL/TP {sl, tp}
  DELETE /positions/:ticket             Close position {volume, deviation}

  GET    /orders                        List pending orders (?symbol=)
  POST   /orders                        Create order {symbol, type, volume, ...}
  GET    /orders/:ticket                Get order
  PUT    /orders/:ticket                Modify order {price, sl, tp}
  DELETE /orders/:ticket                Cancel order

  GET    /history/orders                Order history (?from=TS&to=TS)
  GET    /history/deals                 Deal history (?from=TS&to=TS)
""",
        flush=True,
    )

    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
