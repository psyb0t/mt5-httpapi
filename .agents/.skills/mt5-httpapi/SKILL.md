---
name: mt5-httpapi
description: MetaTrader 5 trading + server-side technical analysis via REST API. Get OHLC/ticks, place/modify/close orders, manage positions, pull history — AND in a single POST get bars enriched with RSI, MACD, Bollinger, ADX, VWAP, Ichimoku, Order Blocks, Fair Value Gaps, BOS/CHoCH, divergences, and dozens more indicators. Use when you need market data, trading, or technical analysis against forex/crypto/stock markets through MT5.
compatibility: Requires curl and a running mt5-httpapi instance. MT5_API_URL env var must be set. MT5_API_TOKEN is optional (only needed if the server has auth configured).
metadata:
  author: psyb0t
  homepage: https://github.com/psyb0t/mt5-httpapi
---

# mt5-httpapi

REST API on top of MetaTrader 5 running inside a Windows VM. Talk to it with plain HTTP/JSON — no MT5 libraries, no Windows, no bullshit. Just curl and go.

**One-stop technical analysis.** Skip the client-side TA stack entirely. `POST /symbols/<symbol>/rates/ta` with an indicator spec → get OHLC bars + analyzed indicator series back in a single call. RSI, MACD, Bollinger, ADX, ATR, VWAP, Ichimoku, Order Blocks, Fair Value Gaps, BOS/CHoCH, swing structure, S/R levels, liquidity, divergences (regular + hidden), session anchors, dozens more — all computed server-side by the [wickworks](https://github.com/psyb0t/docker-wickworks) sidecar that ships with this stack. See the [Technical Analysis](#technical-analysis) section.

For installation and setup, see [references/setup.md](references/setup.md).

## Setup

The API should already be running. Set the base URL and token:

```bash
export MT5_API_URL=http://localhost:8888/roboforex/main
export MT5_API_TOKEN=your_token_here
```

A single nginx sidecar (default `127.0.0.1:8888`) fronts every terminal. The path prefix `/<broker>/<account>/` (matching an entry in `terminals.json`) selects which terminal you talk to — set `MT5_API_URL` to the full base including that prefix. Override the host port with `API_HOST_PORT=...` at compose time.

**Verify:** `curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/ping` — should return `{"status": "ok"}`. If not, the API isn't up yet (may still be initializing — it retries in the background).

Auth is optional — if no token is configured on the server, all requests go through without a token. If a token is configured, all endpoints require `Authorization: Bearer <token>` and return `401` without it.

## How It Works

GET for reading, POST for creating, PUT for modifying, DELETE for closing/canceling. All bodies are JSON.

Every error response:

```json
{"error": "description of what went wrong"}
```

## Pre-Trade Checks (DO NOT SKIP)

Before placing any trade:

1. `GET /account` → `trade_allowed` must be `true`
2. `GET /symbols/SYMBOL` → `trade_mode` must be `4` (full trading)
3. `GET /symbols/SYMBOL` → check `trade_contract_size` — 1 lot of EURUSD = 100,000 EUR, not 1 EUR
4. `GET /terminal` → `connected` must be `true`

## API Reference

### Health

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/ping
# {"status": "ok"}

curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/error
# {"code": 1, "message": "Success"}
```

### Terminal

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/terminal
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/terminal/init
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/terminal/shutdown
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/terminal/restart
```

Key fields on `/terminal`: `connected`, `trade_allowed`, `build`, `company`, `broker_utc_offset_hours` (signed offset applied to all timestamps in/out — see Broker time below).

### Broker time vs real UTC

MT5 returns timestamps in the **broker server's wall-clock time** disguised as unix integers (RoboForex/FTMO = UTC+3, TeleTrade = UTC+2, etc.). The API normalizes this when `utc_offset` is set per terminal in `config/config.yaml`:

```yaml
terminals:
  - broker: roboforex
    account: main
    port: 6542
    utc_offset: "3h"
```

(`port` is container-internal — only nginx and the mt5 container talk to it.)

Forms accepted: `"3h"`, `"3h30m"`, `"-2h"`, `"90m"`, or a bare number (interpreted as hours).

When set, every outgoing time field (tick `time`, rate `time`, position/order/deal `time*` and `time_*_msc`) is real UTC unix, and every incoming `from`/`to` query param is interpreted as real UTC unix. If unset or `0`, raw broker timestamps pass through (legacy behavior). Check `GET /terminal` → `broker_utc_offset_hours` to confirm.

### Account

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/account
```

```json
{
    "login": 12345678,
    "balance": 10000.0,
    "equity": 10000.0,
    "margin": 0.0,
    "margin_free": 10000.0,
    "margin_level": 0.0,
    "leverage": 500,
    "currency": "USD",
    "trade_allowed": true,
    "margin_so_call": 70.0,
    "margin_so_so": 20.0
}
```

### Symbols

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/symbols
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols?group=*USD*"
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/symbols/EURUSD
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/symbols/EURUSD/tick
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H4&count=100"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H1&from=$(date +%s)&count=-100"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H1&from=$(date -d '1 day ago' +%s)&to=$(date +%s)"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/ticks?count=100"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/ticks?from=$(date -d '1 hour ago' +%s)&count=500"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/symbols/EURUSD/ticks?from=$(date -d '1 hour ago' +%s)&to=$(date +%s)"
```

Timeframes: `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1`

Rates/ticks query model — two modes, mutually exclusive:
- **Anchor + signed `count`:** `count=N` = N forward from `from`, `count=-N` = `\|N\|` ending at `from`, `count=0` = empty. Omit `from` to anchor at now.
- **Range (`from` + `to`):** all bars/ticks in the window, no count cap beyond `terminal_info().maxbars`. `to` requires `from` and rejects `count` (returns 400).

`from` and `to` accept three formats (all real UTC): unix seconds (`1700000000`), full datetime `YYYY_MM_DD_HH_MM_SS` (`2024_01_15_14_30_00`), or date-only `YYYY_MM_DD` (midnight UTC).

Capped at `terminal_info().maxbars` rows per request (default 100k — see `GET /terminal`). Symbols auto-select into MarketWatch on first access. Responses are gzipped if the client requests it (`curl --compressed`).

Tick `flags` param: `ALL` (default), `INFO` (bid/ask only — ~10× smaller), `TRADE` (trades only).

Key symbol fields: `bid`, `ask`, `digits`, `point`, `trade_contract_size`, `trade_tick_value`, `trade_tick_size`, `volume_min`, `volume_max`, `volume_step`, `spread`, `swap_long`, `swap_short`, `trade_stops_level`, `trade_mode`.

### Technical Analysis

`POST /symbols/<symbol>/rates/ta` — same query params as `/rates` (timeframe, count, from, to), JSON body carries the [wickworks](https://github.com/psyb0t/docker-wickworks) indicator spec. Response is `{symbol, timeframe, bars, ta}` — OHLC bars *and* analyzed indicator series in one round-trip. No client-side TA library needed.

Full catalog with all indicator types, params, output shapes, and SMC primitives is documented at [github.com/psyb0t/docker-wickworks](https://github.com/psyb0t/docker-wickworks#available-indicators).

```bash
# RSI + MACD + Bollinger Bands on the last 200 H1 bars; tail TA results to last 50.
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "indicators": {
      "rsi": true,
      "macd": true,
      "bbands": {"type": "bbands", "params": {"length": 20, "stddev": 2}}
    },
    "recentBars": 50
  }' \
  "$MT5_API_URL/symbols/EURUSD/rates/ta?timeframe=H1&count=200"
```

Response shape (keys under `ta` mirror the keys in your `indicators` object):

```json
{
  "symbol": "EURUSD",
  "timeframe": "H1",
  "bars": [ { "time": ..., "open": ..., "high": ..., "low": ..., "close": ..., "tick_volume": ... } ],
  "ta": {
    "rsi": [null, null, ..., 54.2, 56.1, 58.7],
    "macd": { "macd": [...], "signal": [...], "hist": [...] },
    "bbands": { "upper": [...], "middle": [...], "lower": [...] }
  }
}
```

**Indicator catalog** (request as `"name": true` for defaults, or `"name": {"type": "...", "params": {...}}` for tuning):

- **Trend**: `sma`, `ema`, `wma`, `dema`, `tema`, `hma`, `kama`, `zlema`, `t3`, `frama`, `vidya`, `mama`, `slope`, `donchian`, `ichimoku`
- **Momentum**: `rsi`, `stoch`, `stochrsi`, `macd`, `cci`, `willr`, `roc`, `mom`, `tsi`, `trix`, `uo`, `fisher`
- **Directional**: `adx`, `aroon`, `supertrend`
- **Volatility**: `atr`, `natr`, `bbands`, `keltner`, `squeeze`
- **Volume**: `vwap` (anchored: session/day/week), `vwma`, `obv`, `ad`, `adosc`, `cmf`, `kvo`, `mfi`
- **SMC / structure**: `orderBlocks`, `fvg` (fair value gaps), `bosChoch`, `swingPoints`, `srLevels`, `liquidity`, `retracements`, `sessions`, `prevHL`
- **Divergences**: `divergence` (regular + hidden, signal-tagged with stable IDs)
- **Summaries**: `position`, `slopeSummary`, `momentumSummary`, `volumeRegime`, `rangeSummary`

Each indicator declares its minimum bar requirement (e.g. `sma(200)` needs 200 bars). If you under-feed it, the server returns HTTP 502 wrapping a wickworks 400 with a per-indicator deficit list — so you see exactly which indicators need more bars, not just a generic "insufficient bars" error.

**Pre-flight tips:**
- Match `count` to your slowest indicator's lookback × 2 (e.g. `sma(200)` → fetch at least 400 bars for warmup + signal).
- Use `recentBars` to limit the tail of the TA response when you only care about the latest few bars; wickworks still computes over the full series so the latest values are correct.
- Combine with `/symbols/:symbol/rates` (raw OHLC) when you need TA for charting separate from trade-decision logic.

### Orders

```bash
# Place market order
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "EURUSD", "type": "BUY", "volume": 0.1, "sl": 1.08, "tp": 1.10}'

# List pending orders
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/orders?symbol=EURUSD"
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders/42094812

# Modify pending order
curl -X PUT -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders/42094812 \
  -H 'Content-Type: application/json' \
  -d '{"price": 1.09, "sl": 1.07, "tp": 1.11}'

# Cancel pending order
curl -X DELETE -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/orders/42094812
```

Order types: `BUY`, `SELL`, `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT`

Fill policies: `FOK`, `IOC` (default), `RETURN`

Expiration: `GTC` (default), `DAY`, `SPECIFIED`, `SPECIFIED_DAY`

Required fields: `symbol`, `type`, `volume`. `price` auto-fills for market orders.

Trade result:

```json
{
    "retcode": 10009,
    "deal": 40536203,
    "order": 42094820,
    "volume": 0.1,
    "price": 1.0950,
    "comment": "Request executed"
}
```

`retcode` 10009 = success. Anything else = something went wrong.

### Positions

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/positions?symbol=EURUSD"
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/42094820

# Update SL/TP
curl -X PUT -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/42094820 \
  -H 'Content-Type: application/json' \
  -d '{"sl": 1.085, "tp": 1.105}'

# Close full position
curl -X DELETE -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/42094820

# Partial close
curl -X DELETE -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/positions/42094820 \
  -H 'Content-Type: application/json' \
  -d '{"volume": 0.05}'
```

Key position fields: `ticket`, `type` (0=buy, 1=sell), `volume`, `price_open`, `price_current`, `sl`, `tp`, `profit`, `swap`.

### History

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/history/orders?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/history/deals?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"
```

`from` and `to` are required, unix epoch seconds.

Deal fields: `type` (0=buy, 1=sell), `entry` (0=opening, 1=closing), `profit` (0 for entries, realized P&L for exits).

## Position Sizing

```
risk_amount     = balance * risk_pct
sl_distance     = ATR * multiplier
ticks_in_sl     = sl_distance / trade_tick_size
risk_per_lot    = ticks_in_sl * trade_tick_value
volume          = risk_amount / risk_per_lot
```

Round down to nearest `volume_step`, clamp to `[volume_min, volume_max]`. Sanity check: `volume * trade_contract_size * price` should make sense relative to account balance.

## Tips

- Always check `retcode` — 10009 = good, anything else = bad
- Use `GET /error` to debug failed trades
- `deviation` on orders = max slippage in points (default 20, raise for volatile markets)
- `type_filling` matters — try `FOK`, `IOC`, `RETURN` if orders get rejected
- Candle `time` is the open time, not close time
- `trade_stops_level` = minimum SL/TP distance from current price in points
- Markets have hours — check `trade_mode` before placing orders
