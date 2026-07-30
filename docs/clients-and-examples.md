# Clients and examples

Examples for REST calls, the public Go client, and technical-analysis workflows.

## Contents

- [Examples](#examples)
- [Go client](#go-client)
- [Technical analysis](#technical-analysis)
- [REST API overview](rest-api.md)
- [Market data API](market-data.md)
- [Trading and history API](trading-and-history.md)
- [Backtesting](backtesting.md)

## Examples

```bash
export MT5_API_URL=http://localhost:8888/roboforex/main
export MT5_API_TOKEN=$(grep ^api_token config/config.yaml | awk -F'"' '{print $2}')  # omit if no auth configured

# Check your balance
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/account

# Grab some EURUSD H4 candles
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H4&count=100"

# YOLO 1000 ADAUSD with SL and TP
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ADAUSD", "type": "BUY", "volume": 1000, "sl": 0.25, "tp": 0.35}'

# Place a pending buy limit
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ADAUSD", "type": "BUY_LIMIT", "volume": 1000, "price": 0.28, "sl": 0.25, "tp": 0.35}'

# Move your SL and TP
curl -X PUT -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/12345 \
  -H "Content-Type: application/json" \
  -d '{"sl": 0.27, "tp": 0.36}'

# Close half
curl -X DELETE -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/12345 \
  -H "Content-Type: application/json" \
  -d '{"volume": 500}'

# Close everything
curl -X DELETE -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/12345

# Hit different terminals when running multi-terminal
curl -H "Authorization: Bearer $MT5_API_TOKEN" http://localhost:8888/roboforex/main/account
curl -H "Authorization: Bearer $MT5_API_TOKEN" http://localhost:8888/ftmo/challenge1/account

# Get deal history for the last 24h
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/history/deals?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"
```

## Go Client

A typed Go client for the live terminal, market-data, order, position, and
history endpoints lives in [`clients/go/`](../clients/go/). Backtest endpoints are
not currently wrapped. Errors map to typed sentinels, and structs have been
verified against live responses.

```bash
go get github.com/psyb0t/mt5-httpapi/clients/go
```

```go
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"time"

	mt5 "github.com/psyb0t/mt5-httpapi/clients/go"
	"github.com/psyb0t/aichteeteapee"
)

func main() {
	c, err := mt5.New(mt5.Config{
		BaseURL: os.Getenv("MT5_API_URL"),
		Token:   os.Getenv("MT5_API_TOKEN"), // empty string if server has no auth
		Timeout: 30 * time.Second,
	})
	if err != nil {
		log.Fatal(err)
	}

	ctx := context.Background()

	acc, err := c.GetAccount(ctx)
	if errors.Is(err, mt5.ErrNotInitialized) {
		log.Fatal("MT5 still booting, retry in a sec")
	}
	if errors.Is(err, aichteeteapee.ErrUnauthorized) {
		log.Fatal("bad token")
	}
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("balance=%.2f %s leverage=1:%d", acc.Balance, acc.Currency, acc.Leverage)

	// Place a market buy
	res, err := c.CreateOrder(ctx, &mt5.CreateOrderRequest{
		Symbol: "EURUSD",
		Type:   "BUY",
		Volume: 0.1,
		SL:     1.08,
		TP:     1.10,
	})
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("retcode=%d order=%d deal=%d price=%.5f", res.Retcode, res.Order, res.Deal, res.Price)

	// Pull H4 candles
	rates, err := c.GetRates(ctx, "EURUSD", mt5.RatesQuery{Timeframe: "H4", Count: 100})
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("got %d candles, last close=%.5f", len(rates), rates[len(rates)-1].Close)
}
```

### Available methods

| Method | Endpoint |
| --- | --- |
| `Ping` | `GET /ping` |
| `LastError` | `GET /error` |
| `GetTerminal` / `InitTerminal` / `ShutdownTerminal` / `RestartTerminal` | `/terminal[/...]` |
| `GetAccount` | `GET /account` |
| `ListSymbols` / `GetSymbol` / `GetTick` / `GetRates` / `GetRatesTA` / `GetTicks` | `/symbols[/...]` |
| `ListOrders` / `CreateOrder` / `GetOrder` / `UpdateOrder` / `CancelOrder` | `/orders[/...]` |
| `ListPositions` / `GetPosition` / `UpdatePosition` / `ClosePosition` | `/positions[/...]` |
| `HistoryOrders` / `HistoryDeals` | `/history/...` |

### Error mapping

HTTP status maps to typed errors you can `errors.Is()` against:

| Status | Error |
| --- | --- |
| 400 | `aichteeteapee.ErrBadRequest` |
| 401 | `aichteeteapee.ErrUnauthorized` |
| 403 | `aichteeteapee.ErrForbidden` |
| 404 | `aichteeteapee.ErrNotFound` |
| 409 | `aichteeteapee.ErrConflict` |
| 422 | `aichteeteapee.ErrUnprocessableEntity` |
| 429 | `aichteeteapee.ErrTooManyRequests` |
| 500 | `aichteeteapee.ErrInternalServer` |
| 502 | `aichteeteapee.ErrBadGateway` |
| 503 | `mt5.ErrNotInitialized` (MT5 still booting) |
| 504 | `aichteeteapee.ErrGatewayTimeout` |

With the import alias used above, `mt5.IsNotInitialized(err)` shortcuts the
common 503 retry case.

## Technical Analysis

Two options:

**Server-side via the wickworks sidecar (`POST /symbols/:symbol/rates/ta`)** — bars come out already enriched with indicators (RSI, MACD, Bollinger Bands, ADX, VWAP, Ichimoku, Order Blocks / FVGs / BOS / CHoCH, swing structure, S/R levels, dozens more). Primitives only — wickworks emits raw indicator series and SMC structural facts; interpretive signals (divergences, crossover events, etc.) belong in your consumer. The wickworks container ships with the docker-compose and is locked to the mt5 net namespace — no external traffic, no separate deploy. See the [endpoint docs](market-data.md#symbols) and the full indicator catalog with params + output shapes at [github.com/psyb0t/docker-wickworks](https://github.com/psyb0t/docker-wickworks).

**Client-side** — grab the raw candles with `GET /rates` and crunch them yourself. There's a full working example in `examples/python/` using [pandas-ta](https://github.com/twopirllc/pandas-ta) with ATR, RSI, MACD, Bollinger Bands, MFI, Stochastic, ADX, VWAP, and moving averages.

```bash
cd examples/python
pip install -r requirements.txt

# Default: EURUSD H4 200 candles
python ta.py

# Custom symbol/timeframe/count
python ta.py BTCUSD H1 100
python ta.py ADAUSD D1 200

# Custom API URL
MT5_API_URL=http://10.0.0.5:8888/roboforex/main python ta.py EURUSD D1

# Candlestick chart with TA overlays (1920x1080 PNG)
python chart.py ADAUSD
python chart.py BTCUSD H1 100
python chart.py EURUSD D1 200 -o eurusd.png
```

Check out `indicators.py` for the individual indicator functions and `signals.py` for signal detection. Use them as building blocks for your own shit.
