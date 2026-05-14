---
name: mt5-httpapi
description: MetaTrader 5 trading + server-side technical analysis via REST API. Get OHLC/ticks, place/modify/close orders, manage positions, pull history — AND in a single POST get bars enriched with RSI, MACD, Bollinger, ADX, VWAP, Ichimoku, Order Blocks, Fair Value Gaps, BOS/CHoCH, swing structure, S/R levels, and dozens more indicators. Use when you need market data, trading, or technical analysis against forex/crypto/stock markets through MT5.
compatibility: Requires curl and a running mt5-httpapi instance. MT5_API_URL env var must be set. MT5_API_TOKEN is optional (only needed if the server has auth configured).
metadata:
  author: psyb0t
  homepage: https://github.com/psyb0t/mt5-httpapi
---

# mt5-httpapi

REST API on top of MetaTrader 5 running inside a Windows VM. Talk to it with plain HTTP/JSON — no MT5 libraries, no Windows, no bullshit. Just curl and go.

**One-stop technical analysis.** Skip the client-side TA stack entirely. `POST /symbols/<symbol>/rates/ta` with an indicator spec → get OHLC bars + analyzed indicator series back in a single call. RSI, MACD, Bollinger, ADX, ATR, VWAP, Ichimoku, Order Blocks, Fair Value Gaps, BOS/CHoCH, swing structure, S/R levels, liquidity, session anchors, dozens more — all computed server-side by the [wickworks](https://github.com/psyb0t/docker-wickworks) sidecar that ships with this stack. Primitives only — wickworks returns raw indicator series and structural facts (e.g. "order block formed at this bar", "price closed past this swing"), never interpretive signals like divergences or crossover events. Build those in the consumer. See the [Technical Analysis](#technical-analysis) section.

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
      "bbands": {"length": 20, "std": 2}
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

**Indicator catalog** (request as `"name": true` for defaults, or `"name": {"length": 21, ...}` for tuning — params are flat on the object; add `"type": "<name>"` only when the output key differs from the indicator name, e.g. running two RSIs as `rsi14` + `rsi21`):

- **Moving averages**: `ema`, `sma`, `hma`, `wma`, `dema`, `tema`, `t3`, `kama`, `alma`, `linreg`, `jma`, `zlma`, `rma`, `fwma`, `swma`, `sinwma`, `trima`, `vwma`, `vwap` (session-anchored: `anchor` D/W/M + `sessionOffset`)
- **Momentum oscillators**: `rsi`, `mfi`, `willr`, `cci`, `roc`, `mom`, `uo`, `stoch`, `stochrsi`, `macd`, `tsi`, `trix`, `fisher`
- **Trend strength & cross-direction**: `adx`, `aroon`, `vortex`
- **Volatility**: `atr`, `natr`
- **Volume / money flow**: `obv`, `ad`, `cmf`, `adosc`, `kvo`
- **Bands & channels**: `bbands`, `kc`, `donchian`
- **Trailing trend signals**: `supertrend`, `psar`, `chandelierExit`, `ichimoku`
- **Compression**: `squeeze` (Bollinger inside Keltner — state machine `on`/`off`/`no` flags)
- **SMC primitives**: `orderBlocks`, `fvg` (alias `fvgs`), `bosChoch`, `swingLevels`, `srLevels`, `recentRange`, `liquidity`, `previousHighLow`, `sessions`, `retracements`
- **Analysis summaries** (last-bar snapshots, shared cached pass — free if you ask for any): `price`, `levels`, `momentum`, `volume`, `position`, `slope`

Wickworks is **primitives-only** as of v0.3.0 — no built-in divergence detection, no MA-cross events, no golden/death-cross tagging. Build those in the consumer over the raw series.

Each indicator declares its minimum bar requirement (e.g. `sma(200)` needs 200 bars). If you under-feed it, the server returns HTTP 502 wrapping a wickworks 400 with a per-indicator deficit list — so you see exactly which indicators need more bars, not just a generic "insufficient bars" error.

**Pre-flight tips:**
- Match `count` to your slowest indicator's lookback × 2 (e.g. `sma(200)` → fetch at least 400 bars for warmup + signal).
- `recentBars` is **inert** in wickworks v0.3.0 — accepted by the request schema but currently unused (reserved for future signal-tagged outputs). To get only the last N bars, set `count` accordingly on the rates query, or slice the response client-side.
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

### Backtest

Run MT5 Strategy Tester via the API. Two-stage workflow: build the INI from a
JSON spec, then submit it together with the `.ex5` (and optional `.set`) for
async execution. Endpoints exist on every terminal but only run on a
`mode: backtest` terminal in `config.yaml` — MT5 is single-instance per
portable data dir, so a tester subprocess collides with a `mode: live`
terminal that already owns the directory and exits silently. The broker/account
in the URL determines which credentials are injected into the run's `[Common]`
section. Only one tester runs at a time per API process; extra submissions
queue.

The expert and set file can be uploaded inline OR referenced by name from a
host-managed pool mounted at `assets/experts/*.ex5` and `assets/sets/*.set`.

```bash
# 1. Build INI: NZDJPY M15, last 5 years, open prices only, 5 ms latency.
curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  $MT5_API_URL/backtest/build-ini \
  -d '{
    "symbol": "NZDJPY",
    "timeframe": "M15",
    "expert": "EA Studio NZDJPY M15 1615044595.ex5",
    "lastYears": 5,
    "modelling": "open-prices",
    "latencyMs": 5,
    "expertParameters": "ea studio nzdjpy m15 1615044595.set"
  }' > tester.ini

# 2. Submit. Use uploads OR host-managed asset names — here, both are host-managed.
JOB=$(curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  $MT5_API_URL/backtest \
  -F "ini=@tester.ini" \
  -F "expert_name=EA Studio NZDJPY M15 1615044595.ex5" \
  -F "set_name=ea studio nzdjpy m15 1615044595.set" \
  | jq -r .jobId)

# 3. Poll. Status is queued → running → completed (or failed).
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/backtest/$JOB

# 4. Fetch the report HTML and the terminal log.
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/backtest/$JOB/report -o report.htm
curl -H "Authorization: Bearer $MT5_API_TOKEN" $MT5_API_URL/backtest/$JOB/log    -o run.log
```

`POST /backtest/build-ini` JSON fields: `symbol`, `timeframe` (`M1`…`MN1`),
`expert` (must end `.ex5`), and exactly one of `fromDate`+`toDate`,
`lastYears`, or `lastDays`. Optional: `modelling` (`every-tick` `1m-ohlc`
`open-prices` `real-ticks`), `latencyMs`, `deposit` (10000), `currency`
(`USD`), `leverage` (100, written as `1:N`), `expertParameters` (`.set`),
`reportName` (`backtest-report.htm`).

`POST /backtest` multipart fields: `ini` (required), one of `expert` or
`expert_name`, optional `set` or `set_name`. Returns `202` with `jobId`,
`statusUrl`, `reportUrl`, `logUrl`, `pollAfterSeconds`, `queuePosition`. The
INI's `[Common]` `Login`/`Password`/`Server` are always overwritten with the
URL-selected account's credentials. Path traversal in `*_name` is rejected.

`GET /backtest/<jobId>` returns the job state. When `status: completed`, the
payload includes a `summary` parsed from the HTML (`netProfit`, `profitFactor`,
`recoveryFactor`, `expectedPayoff`, `sharpeRatio`, `maxDrawdown`,
`totalTrades`, `profitTrades`, `lossTrades`, …). Jobs left running when the
API restarts are marked `failed` on the next startup.

### Real Backtest Runbook For Agents

When the user asks for a real backtest run, do not stop at a built INI or a
`202 Accepted` submit response. The task is only complete after one of these is
true:

- the job reaches `completed`, the report/log are downloaded, and the requested
  summary fields are returned
- a request fails and you report the exact endpoint, HTTP status, and response
  body
- the job reaches `failed` and you report the final status payload exactly

Before submitting a backtest that references host-managed files:

1. Verify the requested filenames exist on disk exactly as named under
   `assets/experts/` and `assets/sets/`.
2. Verify `GET $MT5_API_URL/ping` returns backtest mode on the target terminal.
   For a real tester run, expect `{"status":"ok","mode":"backtest"}`.
3. If the user specifies a concrete date window, prefer explicit UTC
   `fromDate`/`toDate` and do not also send `lastYears` or `lastDays`.
4. If auth is needed and the repo owns the token, read it from `config/config.yaml`
   instead of guessing or waiting for an env var to appear.

Execution guidance:

- Prefer one shell script or one tightly scoped command sequence that performs
  verify -> ping -> build INI -> submit -> poll -> download artifacts. This
  reduces uncertainty from partially completed attempts.
- Persist the local artifacts in a dedicated output directory:
  `tester.ini`, `status.json`, `report.html` (or `.htm`), and `run.log`.
- Treat `queued` and `running` as normal intermediate states. Report the job ID
  and latest status while polling.
- If no `jobId` has been captured yet, there is no confirmed backtest in
  progress. Do not claim the server is still working without that evidence.
- Poll `GET /backtest/<jobId>` using `pollAfterSeconds` from the submit/status
  payload when available. If the user explicitly requests a cadence, follow it.
- On any non-2xx HTTP response, stop immediately and show:
  endpoint, HTTP status, and raw response body.
- On `status: failed`, stop immediately and show the full final status payload.

Completion guidance:

- Download both `reportUrl` and `logUrl` before declaring success.
- Return the requested summary fields directly from the final status payload's
  `summary` object.
- Include the local artifact paths so the user can inspect the exact report and
  terminal log.

Example agent-oriented flow:

```bash
# 0. Verify host-managed assets exactly as named.
test -f "assets/experts/EA.ex5"
test -f "assets/sets/EA.set"

# 1. Health check the target backtest terminal.
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/ping"

# 2. Build the INI from an explicit UTC window.
curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$MT5_API_URL/backtest/build-ini" \
  -d '{
    "symbol": "GBPCAD",
    "timeframe": "M15",
    "expert": "EA.ex5",
    "fromDate": "2021-05-11",
    "toDate": "2026-05-11",
    "modelling": "open-prices",
    "latencyMs": 5,
    "deposit": 1000,
    "currency": "USD"
  }' > tester.ini

# 3. Submit and capture the job ID.
JOB=$(curl -sS -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/backtest" \
  -F "ini=@tester.ini" \
  -F "expert_name=EA.ex5" \
  -F "set_name=EA.set" | jq -r .jobId)

# 4. Poll until completed or failed, then download artifacts.
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/backtest/$JOB"
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/backtest/$JOB/report" -o report.html
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/backtest/$JOB/log" -o run.log
```

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
