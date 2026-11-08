---
title: "REST API"
description: "URL routing per broker and account, bearer auth, health checks, terminal control, account info, broker time and the full endpoint index."
nav_order: 4
has_children: true
---

# REST API — MT5 without the clicky bullshit

One HTTP surface for routing, auth, health, terminal control, account state, and MT5's cursed broker-time timestamps.

## Contents

- [Routing and authentication](#api)
- [All endpoints](#all-endpoints)
- [Errors](#errors)
- [Health](#health)
- [Terminal](#terminal)
- [Account](#account)
- [Broker time vs real UTC](#broker-time-vs-real-utc)
- [Market data](market-data.md)
- [Trading and history](trading-and-history.md)
- [Backtesting](backtesting.md)
- [Compiling MQL5](compiling.md)
- [Chart Deployments](chart-deployments.md)
- [File API](files.md)

## API

nginx puts every terminal behind one loopback-only host port, `http://localhost:8888`. The path tells it which broker/account process gets the request:

```
http://localhost:8888/<broker>/<account>/...
```

When you configure explicit terminal instances, route to them like this:

```
http://localhost:8888/<broker>/<account>/<instance>/...
```

Example: with a `roboforex/main` terminal in `config.yaml`'s `terminals:` list, hit `http://localhost:8888/roboforex/main/ping`. The `/<broker>/<account>/` prefix is stripped by nginx and the rest is proxied to that terminal's API process inside the VM. The `default` instance answers on both `/<broker>/<account>/...` and `/<broker>/<account>/default/...`. A path that matches no configured terminal gets nginx's plain-text `404 no route`.

If `api_token` is set in `config.yaml`, include the token on every request:

```bash
export MT5_API_TOKEN=$(grep ^api_token config/config.yaml | awk -F'"' '{print $2}')
curl -H "Authorization: Bearer $MT5_API_TOKEN" http://localhost:8888/roboforex/main/ping
```

A missing or wrong token gets `401 {"error": "unauthorized", "code": "UNAUTHORIZED"}`. `POST /compile` also takes the compile-only `compile_api_token` and answers its 401 as `{"ok": false, "log": "unauthorized"}`; see [Compiling MQL5](compiling.md).

### All endpoints

Every path below goes after `/<broker>/<account>[/<instance>]`.

| Endpoints | Page |
| --- | --- |
| `/ping`, `/error`, `/busy`, `/terminal`, `/terminal/init`, `/terminal/shutdown`, `/terminal/restart`, `/account` | This page |
| `/symbols`, `/symbols/import`, `/symbols/:symbol`, `/symbols/:symbol/tick`, `/symbols/:symbol/rates`, `/symbols/:symbol/rates/ta`, `/symbols/:symbol/ticks` | [Market data](market-data.md) |
| `/positions`, `/positions/:ticket`, `/orders`, `/orders/:ticket`, `/history/orders`, `/history/deals` | [Trading and history](trading-and-history.md) |
| `/backtest`, `/backtest/build-ini`, `/backtest/build-set`, `/backtest/:job_id`, `/backtest/:job_id/report`, `/backtest/:job_id/log`, `/backtest/:job_id/tail`, `/backtest/:job_id/cancel` | [Backtesting](backtesting.md) |
| `/compile` | [Compiling MQL5](compiling.md) |
| `/files`, `/files/:path`, `/compile/files`, `/compile/files/:path` (only with `files.enabled`) | [File API](files.md) |
| `/experts`, `/sets`, `/deployments`, `/charts`, `/loader`, `/webrequest` and their subpaths (only with `chartctl.enabled`, live mode) | [Chart Deployments](chart-deployments.md) |
| `/mcp` (per-terminal MCP server, same bearer check) | [MCP and agents](mcp-and-agents.md) |

Routes of a feature that is off don't exist, so they answer 404.

### Errors

Errors are JSON with an `error` string. The common ones any route can throw at you (the 503/504 MT5 ones only on routes that call the SDK):

| Status | When |
| --- | --- |
| 401 | Missing or wrong bearer token |
| 413 | Body over `max_request_body_bytes` (4 MiB) or, for uploads, `max_upload_body_bytes` (25 MiB); see [Installation and configuration](installation-and-configuration.md) |
| 503 | `MT5 not initialized`, queue full, or `mt5 call is wedged` (with `Retry-After`); see [Concurrency and backpressure](operations.md#concurrency-and-backpressure) |
| 503 | `REBOOT_PENDING` on a write right before a scheduled reboot (with `Retry-After: 120`) |
| 504 | `mt5 call timed out` |

### Health

| Method | Endpoint | Description       |
| ------ | -------- | ----------------- |
| GET    | `/ping`  | Is this thing on? |
| GET    | `/error` | Last MT5 error    |
| GET    | `/busy`  | Whether a VM reboot now would break work here |

**GET `/ping`** (`mode` is `live` or `backtest`):

```json
{ "status": "ok", "mode": "live", "sdk_threads_alive": 0, "sdk_oldest_alive_s": null }
```

`/ping` takes no MT5 lock, so it answers even while an SDK call is stuck. `sdk_threads_alive` counts SDK calls whose thread has not returned, including ones abandoned after a timeout, and `sdk_oldest_alive_s` is the age of the oldest in seconds, or `null` when there are none. See [Concurrency and backpressure](operations.md#concurrency-and-backpressure).

**GET `/error`**:

```json
{ "code": 1, "message": "Success" }
```

**GET `/busy`**: what this terminal's API is doing that a scheduled VM reboot would break. The reboot waits while any API on the VM says `busy`; see [Scheduled reboots](operations.md#scheduled-reboots).

```json
{
  "busy": true,
  "reasons": ["write_in_flight POST /orders (0.4s)"],
  "backtests": [],
  "writes_in_flight": [{ "method": "POST", "path": "/orders", "seconds": 0.4 }],
  "draining": false
}
```

`backtests` lists this terminal's queued and running backtests, and cancelled ones whose worker is still stopping, as `job_id` and `status`. `writes_in_flight` lists requests other than `GET`, `HEAD` and `OPTIONS` that have not finished. `draining` is true in the seconds before a reboot, when every write answers 503 `REBOOT_PENDING` with `Retry-After: 120`.

### Terminal

| Method | Endpoint             | Description               |
| ------ | -------------------- | ------------------------- |
| GET    | `/terminal`          | Terminal info             |
| POST   | `/terminal/init`     | Initialize MT5 connection |
| POST   | `/terminal/shutdown` | Disconnect this API process from the MT5 SDK |
| POST   | `/terminal/restart`  | Kill and relaunch this terminal process      |

**GET `/terminal`**:

```json
{
  "build": 5602,
  "codepage": 0,
  "commondata_path": "C:\\Users\\Docker\\AppData\\Roaming\\MetaQuotes\\Terminal\\Common",
  "community_account": false,
  "community_balance": 0.0,
  "community_connection": false,
  "company": "Your Broker Inc.",
  "connected": true,
  "data_path": "C:\\Users\\Docker\\Desktop\\Shared\\mybroker",
  "dlls_allowed": true,
  "email_enabled": false,
  "ftp_enabled": false,
  "language": "English",
  "maxbars": 100000,
  "mqid": false,
  "name": "MyBroker MetaTrader 5",
  "notifications_enabled": false,
  "path": "C:\\Users\\Docker\\Desktop\\Shared\\mybroker",
  "ping_last": 0,
  "retransmission": 0.003,
  "trade_allowed": true,
  "tradeapi_disabled": false,
  "broker_utc_offset_hours": 3.0,
  "broker_utc_offset_seconds": 10800
}
```

**POST `/terminal/init`**, **POST `/terminal/shutdown`**, and
**POST `/terminal/restart`**:

```json
{ "success": true }
```

On failure `init` answers 500 `{"success": false, "error": "<MT5 last_error>"}` and `restart` answers 500 `{"success": false, "error": "Terminal restart failed"}`.

The API initializes itself on the first real request. You almost never need to poke these endpoints by hand unless the terminal is being a dick.
`shutdown` leaves `terminal64.exe` running; a later MT5-backed request can
initialize the SDK connection again. `restart` kills and relaunches only the
terminal selected by this API process and may take several minutes.

### Account

| Method | Endpoint   | Description          |
| ------ | ---------- | -------------------- |
| GET    | `/account` | Current account info |

**GET `/account`**:

```json
{
  "login": 12345678,
  "name": "Your Name",
  "server": "MyBroker-Server",
  "company": "Your Broker Inc.",
  "currency": "USD",
  "currency_digits": 2,
  "balance": 10000.0,
  "credit": 0.0,
  "profit": 0.0,
  "equity": 10000.0,
  "margin": 0.0,
  "margin_free": 10000.0,
  "margin_level": 0.0,
  "margin_initial": 0.0,
  "margin_maintenance": 0.0,
  "margin_so_call": 70.0,
  "margin_so_so": 20.0,
  "margin_so_mode": 0,
  "margin_mode": 2,
  "assets": 0.0,
  "liabilities": 0.0,
  "commission_blocked": 0.0,
  "leverage": 500,
  "limit_orders": 0,
  "trade_allowed": true,
  "trade_expert": true,
  "trade_mode": 0,
  "fifo_close": false
}
```

No account logged in: 404 `{"error": "No account logged in"}`.

### Broker time vs real UTC

MT5 has a notorious timezone gotcha: every timestamp it returns (tick `time`, rate `time`, position `time`, deal `time_msc`, etc.) is the **broker server's wall-clock time**, encoded as a unix integer. Looks like UTC, isn't. RoboForex/FTMO run UTC+3, TeleTrade UTC+2 — so a tick captured at real UTC `22:57` reports as unix `01:57` (3h ahead) on RoboForex, `00:57` (2h ahead) on TeleTrade.

The MT5 Python SDK doesn't expose `TimeCurrent()` / `TimeGMT()`, so the API can't auto-detect this. Instead, set `utc_offset` per terminal in `config.yaml`:

```yaml
terminals:
  - broker: roboforex
    account: main
    port: 6542
    utc_offset: "3h"
```

It takes `"3h"`, `"3h30m"`, `"90m"`, `"-2h"` (west of UTC) or a bare number, which counts as hours.

When set, the API:
- Subtracts the offset from every outgoing timestamp (tick time, rate time, position/order/deal times, `_msc` fields), so responses are real UTC unix.
- Adds the offset to incoming `from`/`to` query params and to an order's `expiration` (`POST /orders`, `PUT /orders/:ticket`), so callers always pass real UTC unix and get back real UTC unix.

Inspect via `GET /terminal`: fields `broker_utc_offset_hours` (a float, `3.5` for 3h30m) and `broker_utc_offset_seconds` show what's in effect.

If `utc_offset` is omitted (or `0`), the API passes raw broker timestamps through unchanged (pre-1.8 behavior).
