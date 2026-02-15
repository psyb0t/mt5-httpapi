---
name: mt5-httpapi
description: MetaTrader 5 trading via REST API — get market data, place/modify/close orders, manage positions, pull history. Use when you need to interact with forex/crypto/stock markets through MT5.
homepage: https://github.com/psyb0t/docker-metatrader5-httpapi
user-invocable: true
metadata:
  { "openclaw": { "emoji": "📈", "primaryEnv": "MT5_API_URL", "requires": { "bins": ["curl"] } } }
---

# mt5-httpapi

A REST API sitting on top of MetaTrader 5 running inside a Windows VM. You talk to it with plain HTTP/JSON — no MT5 libraries, no Windows, no bullshit. Just curl and go.

## When To Use This Skill

- You need market data (candles, ticks, symbol info)
- You need to place, modify, or close trades
- You need account info (balance, equity, margin)
- You need to check open positions or pending orders
- You need trade/deal history

## When NOT To Use This Skill

- Technical analysis calculations — do that yourself with the raw candle/tick data
- Charting or visualization — this gives you data, not pictures
- Backtesting — this is live/demo trading only

## Setup

The API should already be running. Set the base URL:

```bash
export MT5_API_URL=http://localhost:6542
```

Or via OpenClaw config (`~/.openclaw/openclaw.json`):

```json
{
  "skills": {
    "entries": {
      "mt5-httpapi": {
        "env": {
          "MT5_API_URL": "http://localhost:6542"
        }
      }
    }
  }
}
```

**Verify:** `curl $MT5_API_URL/ping` — if it responds, you're good. If it doesn't, the API isn't running. Tell the user to set it up: https://github.com/psyb0t/docker-metatrader5-httpapi

## How It Works

Standard REST API. GET for reading, POST for creating, PUT for modifying, DELETE for closing/canceling. All request/response bodies are JSON.

Every error response looks like:

```json
{"error": "description of what went wrong"}
```

## API Reference

### Health

```bash
# Is the API alive?
curl $MT5_API_URL/ping
```

Response:

```json
{"status": "ok"}
```

```bash
# Last MT5 error (useful for debugging failed trades)
curl $MT5_API_URL/error
```

Response:

```json
{"code": 1, "message": "Success"}
```

### Terminal

```bash
# Get terminal info
curl $MT5_API_URL/terminal
```

Response:

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
    "tradeapi_disabled": false
}
```

The most useful fields here: `connected` (is it connected to the broker), `trade_allowed` (can we trade), `company` (which broker).

```bash
# Initialize MT5 connection (usually auto-done, but use this if you get "MT5 not initialized" errors)
curl -X POST $MT5_API_URL/terminal/init
```

Response:

```json
{"success": true}
```

```bash
# Shut down MT5
curl -X POST $MT5_API_URL/terminal/shutdown
```

Response:

```json
{"success": true}
```

You almost never need to call `init` or `shutdown` manually. The API auto-initializes on first request. Only use `init` if something goes sideways, and `shutdown` if you explicitly want to kill the MT5 connection.

### Account

```bash
# Get current account info
curl $MT5_API_URL/account
```

Response:

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

Key fields:
- `balance` — account balance (without open position P&L)
- `equity` — balance + unrealized P&L from open positions
- `margin` — currently used margin
- `margin_free` — available margin for new trades
- `margin_level` — margin level as percentage (equity/margin * 100)
- `leverage` — account leverage (e.g. 500 = 1:500)
- `currency` — account currency (USD, EUR, etc.)
- `trade_allowed` — whether trading is enabled
- `margin_so_call` — margin call level (%)
- `margin_so_so` — stop out level (%)

```bash
# List saved accounts from config (passwords are NOT included)
curl $MT5_API_URL/account/list
```

Response:

```json
{
    "main": {"login": 12345678, "server": "RoboForex-Pro"},
    "demo": {"login": 87654321, "server": "RoboForex-Demo"}
}
```

```bash
# Login with explicit credentials
curl -X POST $MT5_API_URL/account/login \
  -H 'Content-Type: application/json' \
  -d '{"login": 12345678, "password": "pass", "server": "BrokerName-Server"}'
```

```bash
# Login by saved account name (from account.json)
curl -X POST $MT5_API_URL/account/login/demo
```

Login response:

```json
{
    "success": true,
    "login": 87654321,
    "server": "RoboForex-Demo",
    "balance": 10000.0
}
```

### Symbols

```bash
# List all available symbols (returns array of symbol names)
curl $MT5_API_URL/symbols
```

Response:

```json
["EURUSD", "GBPUSD", "ADAUSD", "BTCUSD", ...]
```

```bash
# Filter symbols by group pattern
curl "$MT5_API_URL/symbols?group=*USD*"
```

```bash
# Get full details for a symbol
curl $MT5_API_URL/symbols/EURUSD
```

Response (this is a big one):

```json
{
    "name": "EURUSD",
    "description": "Euro vs US Dollar",
    "path": "Markets\\Forex\\Major\\EURUSD",
    "currency_base": "EUR",
    "currency_profit": "USD",
    "currency_margin": "EUR",
    "digits": 5,
    "point": 1e-05,
    "spread": 30,
    "spread_float": true,
    "trade_contract_size": 100000.0,
    "trade_tick_size": 1e-05,
    "trade_tick_value": 1.0,
    "trade_tick_value_profit": 1.0,
    "trade_tick_value_loss": 1.0,
    "volume_min": 0.01,
    "volume_max": 100.0,
    "volume_step": 0.01,
    "volume_limit": 0.0,
    "trade_mode": 4,
    "trade_calc_mode": 0,
    "trade_exemode": 2,
    "trade_stops_level": 1,
    "trade_freeze_level": 0,
    "swap_long": -11.0,
    "swap_short": 1.14064,
    "swap_mode": 1,
    "swap_rollover3days": 3,
    "margin_initial": 0.0,
    "margin_maintenance": 0.0,
    "margin_hedged": 50000.0,
    "filling_mode": 3,
    "expiration_mode": 15,
    "order_gtc_mode": 0,
    "order_mode": 127,
    "bid": 1.18672,
    "ask": 1.18702,
    "bidhigh": 1.18845,
    "bidlow": 1.1847,
    "askhigh": 1.1885,
    "asklow": 1.18475,
    "last": 0.0,
    "lasthigh": 0.0,
    "lastlow": 0.0,
    "time": 1771027139,
    "volume": 0,
    "volumehigh": 0,
    "volumelow": 0,
    "select": true,
    "visible": true,
    "custom": false,
    "chart_mode": 0,
    "session_deals": 0,
    "session_buy_orders": 0,
    "session_sell_orders": 0,
    "session_buy_orders_volume": 0.0,
    "session_sell_orders_volume": 0.0,
    "session_open": 1.1869,
    "session_close": 1.18698,
    "session_turnover": 0.0,
    "session_volume": 0.0,
    "session_interest": 0.0,
    "session_aw": 0.0,
    "session_price_settlement": 0.0,
    "session_price_limit_min": 0.0,
    "session_price_limit_max": 0.0,
    "price_change": -0.0219,
    "price_volatility": 0.0,
    "price_theoretical": 0.0,
    "price_sensitivity": 0.0,
    "price_greeks_delta": 0.0,
    "price_greeks_theta": 0.0,
    "price_greeks_gamma": 0.0,
    "price_greeks_vega": 0.0,
    "price_greeks_omega": 0.0,
    "price_greeks_rho": 0.0,
    "bank": "",
    "basis": "",
    "category": "",
    "exchange": "",
    "formula": "",
    "isin": "",
    "page": "",
    "trade_accrued_interest": 0.0,
    "trade_face_value": 0.0,
    "trade_liquidity_rate": 0.0,
    "margin_hedged_use_leg": false,
    "ticks_bookdepth": 16
}
```

Key fields for trading:
- `bid`, `ask` — current prices
- `digits` — price decimal places
- `point` — smallest price change (e.g. 0.00001 for 5-digit forex)
- `trade_tick_size` — minimum price movement
- `trade_tick_value` — profit/loss per tick per 1 lot
- `trade_contract_size` — contract size (e.g. 100000 for forex)
- `volume_min`, `volume_max`, `volume_step` — lot size constraints
- `spread` — current spread in points
- `swap_long`, `swap_short` — overnight swap rates
- `trade_stops_level` — minimum distance for SL/TP from current price (in points, 0 = no limit)
- `trade_freeze_level` — distance from current price where orders can't be modified (in points)

```bash
# Get latest tick (bid/ask snapshot)
curl $MT5_API_URL/symbols/EURUSD/tick
```

Response:

```json
{
    "time": 1771146325,
    "bid": 0.2973,
    "ask": 0.2976,
    "last": 0.0,
    "volume": 0,
    "time_msc": 1771146325123,
    "flags": 6,
    "volume_real": 0.0
}
```

### Market Data — Candles (OHLCV)

```bash
# Get 100 M1 candles (default)
curl $MT5_API_URL/symbols/EURUSD/rates

# Get 200 H4 candles
curl "$MT5_API_URL/symbols/EURUSD/rates?timeframe=H4&count=200"
```

Available timeframes: `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1`

Response (array of candles):

```json
[
    {
        "time": 1771128000,
        "open": 0.2962,
        "high": 0.3006,
        "low": 0.2922,
        "close": 0.2979,
        "tick_volume": 4755,
        "spread": 30,
        "real_volume": 0
    }
]
```

- `time` is the candle **open** time, unix epoch seconds
- `tick_volume` is the number of ticks in the candle (use as volume proxy for forex)
- `real_volume` is exchange-reported volume (0 for most forex)

### Market Data — Ticks

```bash
# Get last 100 ticks
curl $MT5_API_URL/symbols/EURUSD/ticks

# Get last 500 ticks
curl "$MT5_API_URL/symbols/EURUSD/ticks?count=500"
```

Response (array of ticks):

```json
[
    {
        "time": 1771146325,
        "bid": 0.2973,
        "ask": 0.2976,
        "last": 0.0,
        "volume": 0,
        "time_msc": 1771146325123,
        "flags": 6,
        "volume_real": 0.0
    }
]
```

- `time` — unix epoch seconds
- `time_msc` — same timestamp in milliseconds for higher precision

### Placing Orders

```bash
# Market buy
curl -X POST $MT5_API_URL/orders \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "ADAUSD", "type": "BUY", "volume": 1000}'

# Market buy with SL and TP
curl -X POST $MT5_API_URL/orders \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "ADAUSD", "type": "BUY", "volume": 1000, "sl": 0.25, "tp": 0.35}'

# Market sell
curl -X POST $MT5_API_URL/orders \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "ADAUSD", "type": "SELL", "volume": 1000}'

# Pending buy limit (triggers when price drops to 0.28)
curl -X POST $MT5_API_URL/orders \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "ADAUSD", "type": "BUY_LIMIT", "volume": 1000, "price": 0.28, "sl": 0.25, "tp": 0.35}'
```

Full order body:

```json
{
    "symbol": "ADAUSD",
    "type": "BUY",
    "volume": 1000,
    "price": 0.28,
    "sl": 0.25,
    "tp": 0.35,
    "deviation": 20,
    "magic": 0,
    "comment": "",
    "type_filling": "IOC",
    "type_time": "GTC"
}
```

- Required: `symbol`, `type`, `volume`. Everything else is optional.
- `price` gets auto-filled for market orders (uses current ask for BUY, bid for SELL).
- `deviation` — max price slippage in points. If price moves more than this between request and execution, order gets rejected. Default: 20.
- `magic` — expert advisor ID, use to tag orders from different strategies.
- `type_filling` — how to fill the order if full volume isn't available.
- `type_time` — when the order expires.

Order types:
- Market: `BUY`, `SELL`
- Pending: `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT`

Fill policies: `FOK` (fill or kill — all or nothing), `IOC` (immediate or cancel — fill what you can, cancel rest, **default**), `RETURN` (fill what you can, leave rest as order)

Expiration: `GTC` (good till cancelled, **default**), `DAY` (expires end of day), `SPECIFIED` (expires at specific time), `SPECIFIED_DAY` (expires at specific day)

**Trade result** (returned on success):

```json
{
    "retcode": 10009,
    "deal": 40536194,
    "order": 42094812,
    "volume": 3100.0,
    "price": 0.2989,
    "bid": 0.2986,
    "ask": 0.2989,
    "comment": "Request executed",
    "request_id": 1549268248,
    "retcode_external": 0
}
```

- `retcode` 10009 = success. Anything else = something went wrong, check `comment`.
- `deal` — deal ticket (unique ID for the executed trade)
- `order` — order ticket
- `volume` — actually executed volume
- `price` — execution price

### Managing Pending Orders

```bash
# List all pending orders
curl $MT5_API_URL/orders

# Filter by symbol
curl "$MT5_API_URL/orders?symbol=EURUSD"

# Get specific order
curl $MT5_API_URL/orders/42094812
```

Pending order object:

```json
{
    "ticket": 42094812,
    "time_setup": 1771147800,
    "time_setup_msc": 1771147800123,
    "time_done": 0,
    "time_done_msc": 0,
    "time_expiration": 0,
    "type": 2,
    "type_time": 0,
    "type_filling": 1,
    "state": 1,
    "magic": 0,
    "position_id": 0,
    "position_by_id": 0,
    "reason": 3,
    "volume_initial": 1000.0,
    "volume_current": 1000.0,
    "price_open": 0.28,
    "sl": 0.25,
    "tp": 0.35,
    "price_current": 0.2989,
    "price_stoplimit": 0.0,
    "symbol": "ADAUSD",
    "comment": "",
    "external_id": ""
}
```

Key fields:
- `ticket` — unique order ID, use this for modify/cancel
- `type` — order type (0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP, 6=BUY_STOP_LIMIT, 7=SELL_STOP_LIMIT)
- `volume_initial` — originally requested volume
- `volume_current` — remaining volume (less if partially filled)
- `price_open` — order price
- `sl`, `tp` — stop loss and take profit
- `price_current` — current market price
- `state` — order state (1=placed, 2=canceled, 3=partial, 4=filled, 5=rejected, 6=expired)

```bash
# Modify a pending order (change price, SL, TP)
curl -X PUT $MT5_API_URL/orders/42094812 \
  -H 'Content-Type: application/json' \
  -d '{"price": 0.29, "sl": 0.26, "tp": 0.36}'
```

All fields optional. Only pass what you want to change.

```bash
# Cancel a pending order
curl -X DELETE $MT5_API_URL/orders/42094812
```

Both return a trade result object (see above).

### Managing Positions

```bash
# List all open positions
curl $MT5_API_URL/positions

# Filter by symbol
curl "$MT5_API_URL/positions?symbol=ADAUSD"

# Get specific position
curl $MT5_API_URL/positions/42094812
```

Position object:

```json
{
    "ticket": 42094812,
    "time": 1771147866,
    "time_msc": 1771147866130,
    "time_update": 1771147866,
    "time_update_msc": 1771147866130,
    "type": 0,
    "magic": 0,
    "identifier": 42094812,
    "reason": 3,
    "volume": 3100.0,
    "price_open": 0.2989,
    "sl": 0.25,
    "tp": 0.35,
    "price_current": 0.2991,
    "swap": 0.0,
    "profit": 6.2,
    "symbol": "ADAUSD",
    "comment": "",
    "external_id": ""
}
```

Key fields:
- `ticket` — unique position ID, use this for update/close
- `type` — 0 = buy, 1 = sell
- `volume` — current position size
- `price_open` — entry price
- `price_current` — current market price
- `sl`, `tp` — stop loss and take profit (0.0 = not set)
- `swap` — accumulated swap
- `profit` — unrealized P&L in account currency
- `time` — when the position was opened (unix epoch seconds)

```bash
# Update SL/TP on an open position
curl -X PUT $MT5_API_URL/positions/42094812 \
  -H 'Content-Type: application/json' \
  -d '{"sl": 0.27, "tp": 0.36}'
```

All fields optional. Only pass what you want to change.

```bash
# Close entire position
curl -X DELETE $MT5_API_URL/positions/42094812

# Partial close (close 500 out of 3100 volume)
curl -X DELETE $MT5_API_URL/positions/42094812 \
  -H 'Content-Type: application/json' \
  -d '{"volume": 500}'

# Close with custom deviation (max price slippage)
curl -X DELETE $MT5_API_URL/positions/42094812 \
  -H 'Content-Type: application/json' \
  -d '{"deviation": 50}'
```

All fields optional. `volume` defaults to full position, `deviation` defaults to 20.

Both update and close return a trade result object (see above).

### History

Both endpoints require `from` and `to` as unix epoch seconds.

```bash
# Get order history for the last 24 hours
curl "$MT5_API_URL/history/orders?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"

# Get deal history for the last 24 hours
curl "$MT5_API_URL/history/deals?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"

# Get deal history for a specific range
curl "$MT5_API_URL/history/deals?from=1771060000&to=1771150000"
```

History order object (completed/cancelled orders):

```json
{
    "ticket": 42094812,
    "time_setup": 1771147800,
    "time_setup_msc": 1771147800123,
    "time_done": 1771147866,
    "time_done_msc": 1771147866130,
    "time_expiration": 0,
    "type": 0,
    "type_time": 0,
    "type_filling": 1,
    "state": 4,
    "magic": 0,
    "position_id": 42094812,
    "position_by_id": 0,
    "reason": 3,
    "volume_initial": 3100.0,
    "volume_current": 0.0,
    "price_open": 0.2989,
    "sl": 0.25,
    "tp": 0.35,
    "price_current": 0.2989,
    "price_stoplimit": 0.0,
    "symbol": "ADAUSD",
    "comment": "Request executed",
    "external_id": ""
}
```

- `state` 4 = filled, 2 = canceled, 5 = rejected, 6 = expired
- `time_setup` = when the order was placed, `time_done` = when it was executed/cancelled
- `volume_current` = 0 means fully filled

Deal object (actual executed trades):

```json
{
    "ticket": 40536194,
    "order": 42094812,
    "time": 1771147866,
    "time_msc": 1771147866130,
    "type": 0,
    "entry": 0,
    "position_id": 42094812,
    "symbol": "ADAUSD",
    "volume": 3100.0,
    "price": 0.2989,
    "commission": 0.0,
    "swap": 0.0,
    "profit": 0.0,
    "fee": 0.0,
    "magic": 0,
    "reason": 3,
    "comment": "",
    "external_id": ""
}
```

- `type` — 0 = buy, 1 = sell
- `entry` — 0 = entry (opening), 1 = exit (closing), 2 = reverse, 3 = close by opposite
- `profit` — realized P&L for this deal (0 for entries, actual P&L for exits)
- `commission`, `swap`, `fee` — trading costs
- `position_id` — links the deal to a position
- `order` — links the deal to the order that triggered it

## Pre-Trade Checks (DO NOT SKIP)

Before you place ANY trade, you MUST verify these things. Skipping this can lose real money.

1. **Check if trading is enabled on the account:** `GET /account` → `trade_allowed` must be `true`. If it's `false`, you can't trade — don't even try.

2. **Check if the symbol is open for trading:** `GET /symbols/SYMBOL` → check `trade_mode`. It must be 4 (full trading). Other values mean trading is restricted or disabled for that symbol. Markets have trading hours — forex is closed on weekends, stocks have exchange hours, crypto is usually 24/7 but not always.

3. **Check the contract size:** `GET /symbols/SYMBOL` → `trade_contract_size`. This is critical. For forex it's usually 100,000 (meaning 1 lot = 100,000 units of the base currency). For crypto it might be 1 (meaning 1 lot = 1 coin). If you blindly send `"volume": 1000` thinking it's 1000 coins but the contract size is 100,000, you just opened a position worth 100,000,000 units. **Always check `trade_contract_size` and factor it into your position sizing.**

4. **Check the terminal connection:** `GET /terminal` → `connected` must be `true`. If the terminal is disconnected from the broker, orders will fail.

## Typical Workflow

1. **Check the connection:** `GET /ping` — make sure the API responds
2. **Check your account:** `GET /account` — verify `trade_allowed` is `true`, check `balance`, `equity`, `margin_free`
3. **Check the terminal:** `GET /terminal` — verify `connected` is `true`
4. **Get symbol specs:** `GET /symbols/SYMBOL` — verify `trade_mode` is 4 (trading enabled), check `trade_contract_size`, `trade_tick_value`, `volume_min`, `volume_step`
5. **Get market data:** `GET /symbols/SYMBOL/rates?timeframe=H4&count=100` — pull candles for analysis
6. **Get current price:** `GET /symbols/SYMBOL/tick` — latest bid/ask
7. **Calculate your position size and risk** — use `trade_contract_size` and `trade_tick_value` from step 4. Don't guess.
8. **Place the trade:** `POST /orders` with your symbol, type, volume, SL, TP
9. **Monitor:** `GET /positions` to check open positions
10. **Adjust:** `PUT /positions/:ticket` to move SL/TP
11. **Close:** `DELETE /positions/:ticket` when you're done
12. **Review:** `GET /history/deals` to check what happened

## Position Sizing

Say you want to risk 1% of your account on a trade with a stop loss at 3x ATR on H4:

1. Get account balance: `GET /account` → `balance` field. Also verify `trade_allowed` is `true`.
2. Get symbol specs: `GET /symbols/ADAUSD` → grab `trade_contract_size`, `trade_tick_value`, `trade_tick_size`, `volume_min`, `volume_max`, `volume_step`. Also verify `trade_mode` is 4.
3. Get candles: `GET /symbols/ADAUSD/rates?timeframe=H4&count=15` → calculate ATR from high/low/close data (average true range over 14 periods)
4. Calculate:
   - `risk_amount = balance * 0.01`
   - `sl_distance = ATR * 3`
   - `ticks_in_sl = sl_distance / trade_tick_size`
   - `risk_per_lot = ticks_in_sl * trade_tick_value` (this is how much you lose per 1 lot if SL is hit)
   - `volume = risk_amount / risk_per_lot`
   - **Sanity check:** `notional_value = volume * trade_contract_size * current_price` — make sure this isn't insane relative to your balance
5. Round volume down to nearest `volume_step`, clamp between `volume_min` and `volume_max`
6. Get current price: `GET /symbols/ADAUSD/tick` → use `ask` for buy, `bid` for sell
7. Calculate SL price: `entry_price - sl_distance` for buy, `entry_price + sl_distance` for sell
8. Send the order: `POST /orders` with symbol, type, volume, sl

## Technical Analysis

This API gives you raw market data — it does NOT do TA for you. If you need indicators (ATR, RSI, MACD, Bollinger Bands, moving averages, etc.), grab the candle data from here and crunch it yourself.

There's a full working example in the repo at `examples/ta/` using [pandas-ta](https://github.com/twopirllc/pandas-ta) — check `indicators.py` for individual indicator functions, `signals.py` for signal detection, and `ta.py` to run it all.

### Quick example

```python
import pandas as pd
import pandas_ta as ta
import requests

# Grab candles from the API
candles = requests.get("http://localhost:6542/symbols/EURUSD/rates?timeframe=H4&count=200").json()
df = pd.DataFrame(candles)

# ATR, RSI, MACD, Bollinger Bands, moving averages — one-liners
df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
df["rsi"] = ta.rsi(df["close"], length=14)
df = pd.concat([df, ta.macd(df["close"])], axis=1)
df = pd.concat([df, ta.bbands(df["close"], length=20, std=2)], axis=1)
df["sma_50"] = ta.sma(df["close"], length=50)
df["ema_21"] = ta.ema(df["close"], length=21)
df["mfi"] = ta.mfi(df["high"], df["low"], df["close"], df["tick_volume"], length=14)
```

Install: `pip install requests pandas pandas-ta`

## Tips

1. **ALWAYS check if trading is enabled before placing orders** — `GET /account` → `trade_allowed` must be `true`. `GET /symbols/SYMBOL` → `trade_mode` must be 4. If either is wrong, the trade will fail or worse — you'll get errors you don't understand.
2. **ALWAYS check `trade_contract_size` before sizing your position** — this is the single easiest way to blow an account by accident. 1 lot of EURUSD = 100,000 EUR. 1 lot of BTCUSD might = 1 BTC. 1 lot of some index might = 10 contracts. Never assume. Always check.
3. **ALWAYS check `retcode` in trade results** — 10009 means success, anything else is a problem
4. **Use `GET /error` to debug** — when a trade fails, this tells you what MT5 is complaining about
5. **Demo accounts first** — test your shit before going live
6. **Markets have hours** — forex is closed on weekends (Friday ~22:00 UTC to Sunday ~22:00 UTC). Stocks follow exchange hours. Crypto is usually 24/7 but check the symbol's `trade_mode` to be sure. If you try to trade when the market is closed, the order will be rejected.
7. **`deviation` matters for market orders** — if price moves more than `deviation` points between your request and execution, the order gets rejected. Default is 20, increase it for volatile markets.
8. **`type_filling` matters** — some brokers only support certain fill policies. If your order gets rejected, try switching between `FOK`, `IOC`, and `RETURN`.
9. **Partial closes are a thing** — you don't have to close an entire position, pass `volume` in the DELETE body
10. **History needs explicit time range** — `from` and `to` are required, both unix epoch seconds
11. **Candle `time` is the candle open time** — not close time
12. **`time_msc` is milliseconds** — `time` is seconds, `time_msc` is the same timestamp in milliseconds
13. **Check `trade_stops_level` on the symbol** — this is the minimum distance (in points) between current price and your SL/TP. If it's 10 and the point is 0.00001, your SL/TP must be at least 0.0001 away from current price.
14. **Check `volume_step` before placing orders** — if `volume_step` is 0.01, you can't trade 0.015 lots. Round to the nearest step.
15. **`margin_free` tells you how much you can trade** — if it's 0 or close to 0, you're maxed out
16. **`profit` on positions is unrealized** — it changes with every tick. `profit` on deals is realized (final).
17. **Do a sanity check on notional value** — before sending any order, calculate `volume * trade_contract_size * price` and make sure it's not absurd relative to the account balance. If your $10,000 account is about to open a $5,000,000 notional position, something is wrong with your math.
