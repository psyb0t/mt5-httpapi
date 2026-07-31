# REST API — MT5 without the clicky bullshit

One HTTP surface for routing, auth, health, terminal control, account state, and MT5's cursed broker-time timestamps.

## Contents

- [Routing and authentication](#api)
- [Health](#health)
- [Terminal](#terminal)
- [Account](#account)
- [Broker time vs real UTC](#broker-time-vs-real-utc)
- [Market data](market-data.md)
- [Trading and history](trading-and-history.md)
- [Backtesting](backtesting.md)

## API

nginx puts every terminal behind one loopback-only host port, `http://localhost:8888`. The path tells it which broker/account process gets the request:

```
http://localhost:8888/<broker>/<account>/...
```

When you configure explicit terminal instances, route to them like this:

```
http://localhost:8888/<broker>/<account>/<instance>/...
```

Example: with a `roboforex/main` terminal in `config.yaml`'s `terminals:` list, hit `http://localhost:8888/roboforex/main/ping`. The `/<broker>/<account>/` prefix is stripped by nginx and the rest is proxied to that terminal's API process inside the VM.

If `api_token` is set in `config.yaml`, include the token on every request:

```bash
export MT5_API_TOKEN=$(grep ^api_token config/config.yaml | awk -F'"' '{print $2}')
curl -H "Authorization: Bearer $MT5_API_TOKEN" http://localhost:8888/roboforex/main/ping
```

### Health

| Method | Endpoint | Description       |
| ------ | -------- | ----------------- |
| GET    | `/ping`  | Is this thing on? |
| GET    | `/error` | Last MT5 error    |

**GET `/ping`** (`mode` is `live` or `backtest`):

```json
{ "status": "ok", "mode": "live" }
```

**GET `/error`**:

```json
{ "code": 1, "message": "Success" }
```

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
  "broker_utc_offset_hours": 3,
  "broker_utc_offset_seconds": 10800
}
```

**POST `/terminal/init`**, **POST `/terminal/shutdown`**, and
**POST `/terminal/restart`**:

```json
{ "success": true }
```

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

When set, the API:
- Subtracts the offset from every outgoing timestamp (tick time, rate time, position/order/deal times, `_msc` fields), so responses are real UTC unix.
- Adds the offset to incoming `from`/`to` query params, so callers always pass real UTC unix and get back real UTC unix.

Inspect via `GET /terminal` — fields `broker_utc_offset_hours` and `broker_utc_offset_seconds` show what's in effect.

If `utc_offset` is omitted (or `0`), the API passes raw broker timestamps through unchanged (pre-1.8 behavior).
