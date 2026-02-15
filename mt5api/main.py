import sys
import threading

import MetaTrader5 as mt5

from mt5api.config import HOST, PORT, TERMINAL_PATH
from mt5api.server import app

INIT_TIMEOUT = 60


def _try_initialize():
    """Try to initialize MT5 with a timeout so it doesn't hang forever."""
    result = [None]

    def _init():
        result[0] = mt5.initialize(path=TERMINAL_PATH)

    t = threading.Thread(target=_init, daemon=True)
    t.start()
    t.join(timeout=INIT_TIMEOUT)

    if t.is_alive():
        msg = f"WARNING: mt5.initialize() hung for {INIT_TIMEOUT}s - starting server anyway"
        print(msg, flush=True)
        print(msg, file=sys.stderr, flush=True)
        return False

    if not result[0]:
        err = mt5.last_error()
        msg = f"WARNING: MT5 init failed: {err}"
        print(msg, flush=True)
        print(msg, file=sys.stderr, flush=True)
        return False

    return True


def main():
    print(f"Initializing MT5 from {TERMINAL_PATH} (timeout {INIT_TIMEOUT}s)...", flush=True)
    if _try_initialize():
        info = mt5.terminal_info()
        print(f"MT5 connected: build {info.build}", flush=True)
    else:
        print("Server will start anyway - call POST /terminal/init to retry.", flush=True)

    print(f"\nMT5 HTTP API listening on {HOST}:{PORT}", flush=True)
    print("""
Endpoints:
  GET    /ping                          Health check
  GET    /error                         Last MT5 error

  GET    /terminal                      Terminal info
  POST   /terminal/init                 Initialize MT5
  POST   /terminal/shutdown             Shutdown MT5

  GET    /account                       Account info
  POST   /account/login                 Login {login, password, server}

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
""", flush=True)

    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
