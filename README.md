# mt5-httpapi

MetaTrader 5 running inside a real Windows VM (Docker + QEMU/KVM) with a REST API slapped on top for programmatic trading. No Wine bullshit, no janky workarounds - a legit Windows environment running the full MT5 terminal in portable mode.

Supports multiple brokers on the same VM. Throw in your broker's MT5 installers, set up your accounts, switch between them by editing `config/terminal.json` and restarting. Done.

## ⚠️ Disclaimer

This is a tool for automating trades. If you blow your account, that's on you. Use demo accounts first, test your shit, and don't come crying when your algo buys the top.

## Recommended Brokers

- [RoboForex](https://my.roboforex.com/en/?a=zswg) — solid execution, crypto + forex, up to 1:2000 leverage, demo accounts available

## Requirements

- Linux host with KVM enabled (`/dev/kvm`)
- Docker + Docker Compose
- ~10 GB disk (Windows ISO + VM storage)
- 5 GB RAM (for the Windows VM)

## Quick Start

```bash
# 1. Set up your broker account
cp config/account.json.example config/account.json
cp config/terminal.example.json config/terminal.json
# Edit both files with your broker credentials

# 2. Drop your broker's MT5 installer in mt5installers/
#    Name it: mt5setup-<broker>.exe
cp ~/Downloads/mt5setup.exe mt5installers/mt5setup-roboforex.exe

# 3. Fire it up
make up
```

First run downloads [tiny11](https://archive.org/details/tiny-11-NTDEV) (stripped-down Windows 11, ~4 GB), installs it (~10 min), then sets up Python + MT5 automatically. After that, boots in ~1 min. Go grab a coffee on the first run.

If you'd rather use your own Windows ISO, just drop it at `data/win.iso` and it'll skip the download.

## Configuration

### `config/account.json`

Your broker credentials. Organized by broker, then account name:

```json
{
    "roboforex": {
        "main": {
            "login": 12345678,
            "password": "your_password",
            "server": "RoboForex-Pro"
        },
        "demo": {
            "login": 87654321,
            "password": "demo_password",
            "server": "RoboForex-Demo"
        }
    }
}
```

### `config/terminal.json`

Tells the system which broker and account to use:

```json
{
    "broker": "roboforex",
    "account": "main"
}
```

### `config/requirements.txt`

Extra Python packages you want in the VM. `MetaTrader5` and `flask` are already in there.

### `config/setup.bat`

Custom commands that run on every VM boot before MT5 starts. Shove whatever Windows setup shit you need in here.

### `mt5installers/`

Dump your broker MT5 installers here. Name them `mt5setup-<broker>.exe` and each one gets its own portable install automatically.

## API

Default: `http://localhost:6542`

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/ping` | Is this thing on? |
| GET | `/error` | Last MT5 error |

**GET `/ping`**:

```json
{"status": "ok"}
```

**GET `/error`**:

```json
{"code": 1, "message": "Success"}
```

### Terminal

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/terminal` | Terminal info |
| POST | `/terminal/init` | Initialize MT5 connection |
| POST | `/terminal/shutdown` | Kill MT5 |

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
    "tradeapi_disabled": false
}
```

**POST `/terminal/init`** and **POST `/terminal/shutdown`**:

```json
{"success": true}
```

The API auto-initializes on first request. You almost never need to call these manually.

### Account

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/account` | Current account info |
| GET | `/account/list` | List saved accounts |
| POST | `/account/login` | Login with `{login, password, server}` |
| POST | `/account/login/:name` | Login by saved account name |

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

**GET `/account/list`** (passwords not included):

```json
{
    "main": {"login": 12345678, "server": "RoboForex-Pro"},
    "demo": {"login": 87654321, "server": "RoboForex-Demo"}
}
```

**POST `/account/login`** and **POST `/account/login/:name`**:

```json
{
    "success": true,
    "login": 87654321,
    "server": "RoboForex-Demo",
    "balance": 10000.0
}
```

### Symbols

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/symbols` | List symbols (`?group=*USD*`) |
| GET | `/symbols/:symbol` | Symbol details |
| GET | `/symbols/:symbol/tick` | Latest tick |
| GET | `/symbols/:symbol/rates` | OHLCV candles (`?timeframe=H1&count=100`) |
| GET | `/symbols/:symbol/ticks` | Tick data (`?count=100`) |

**GET `/symbols`** — array of symbol names:

```json
["EURUSD", "GBPUSD", "ADAUSD", "BTCUSD", "..."]
```

**GET `/symbols/:symbol`** — full symbol info:

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
    "time": 1771027139,
    "select": true,
    "visible": true,
    "custom": false,
    "session_deals": 0,
    "session_buy_orders": 0,
    "session_sell_orders": 0,
    "session_open": 1.1869,
    "session_close": 1.18698,
    "price_change": -0.0219,
    "bank": "",
    "basis": "",
    "category": "",
    "exchange": "",
    "isin": "",
    "..."
}
```

There's a shitload of fields — these are the ones you'll actually use:

| Field | What it is |
|-------|-----------|
| `bid`, `ask` | Current prices |
| `digits` | Price decimal places |
| `point` | Smallest price change |
| `trade_tick_size` | Minimum price movement |
| `trade_tick_value` | Profit/loss per tick per 1 lot |
| `trade_contract_size` | Contract size (100000 for forex) |
| `volume_min`, `volume_max`, `volume_step` | Lot size constraints |
| `spread` | Current spread in points |
| `swap_long`, `swap_short` | Overnight swap rates |
| `trade_stops_level` | Min distance for SL/TP from price (points) |

**GET `/symbols/:symbol/tick`**:

```json
{
    "time": 1771150549,
    "bid": 0.3001,
    "ask": 0.3004,
    "last": 0.0,
    "volume": 0,
    "time_msc": 1771150549145,
    "flags": 1030,
    "volume_real": 0.0
}
```

**GET `/symbols/:symbol/rates`** — array of OHLCV candles:

Timeframes: `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1`

```json
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
```

`time` is the candle open time, unix epoch seconds.

**GET `/symbols/:symbol/ticks`** — array of ticks:

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

### Positions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/positions` | List open positions (`?symbol=`) |
| GET | `/positions/:ticket` | Get position |
| PUT | `/positions/:ticket` | Update SL/TP |
| DELETE | `/positions/:ticket` | Close position |

**GET `/positions`** — array of position objects:

```json
{
    "ticket": 42094820,
    "time": 1771150554,
    "time_msc": 1771150554509,
    "time_update": 1771150554,
    "time_update_msc": 1771150554509,
    "type": 0,
    "magic": 0,
    "identifier": 42094820,
    "reason": 3,
    "volume": 100.0,
    "price_open": 0.3005,
    "sl": 0.28,
    "tp": 0.32,
    "price_current": 0.3003,
    "swap": 0.0,
    "profit": -0.02,
    "symbol": "ADAUSD",
    "comment": "",
    "external_id": ""
}
```

`type` 0 = buy, 1 = sell. `profit` is unrealized P&L.

**PUT `/positions/:ticket`** — move your stop loss / take profit:

```json
{
    "sl": 0.27,
    "tp": 0.36
}
```

**DELETE `/positions/:ticket`** — close that shit:

```json
{
    "volume": 500,
    "deviation": 20
}
```

All fields optional. `volume` defaults to full position, `deviation` defaults to 20.

### Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/orders` | List pending orders (`?symbol=`) |
| POST | `/orders` | Place an order |
| GET | `/orders/:ticket` | Get order |
| PUT | `/orders/:ticket` | Modify order |
| DELETE | `/orders/:ticket` | Cancel order |

**GET `/orders`** — array of pending order objects:

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

`type`: 0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP. `state`: 1=placed, 2=canceled, 3=partial, 4=filled, 5=rejected, 6=expired.

**POST `/orders`** — send it:

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

Required: `symbol`, `type`, `volume`. Everything else is optional. `price` gets auto-filled for market orders.

Order types:
- Market: `BUY`, `SELL`
- Pending: `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT`

Fill policies: `FOK`, `IOC` (default), `RETURN`

Expiration types: `GTC` (default), `DAY`, `SPECIFIED`, `SPECIFIED_DAY`

**PUT `/orders/:ticket`** — change your mind on a pending order:

```json
{
    "price": 0.29,
    "sl": 0.26,
    "tp": 0.36,
    "type_time": "GTC"
}
```

All fields optional.

### Trade Result

What comes back from POST/PUT/DELETE on orders and positions:

```json
{
    "retcode": 10009,
    "deal": 40536203,
    "order": 42094820,
    "volume": 100.0,
    "price": 0.3005,
    "bid": 0.3002,
    "ask": 0.3005,
    "comment": "Request executed",
    "request_id": 1549268253,
    "retcode_external": 0
}
```

`retcode` 10009 = you're good. Anything else = something went wrong.

### History

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/history/orders` | Order history (`?from=TS&to=TS`) |
| GET | `/history/deals` | Deal history (`?from=TS&to=TS`) |

`from` and `to` are required, unix epoch seconds.

**History order object** (completed/cancelled orders):

```json
{
    "ticket": 42094820,
    "time_setup": 1771150554,
    "time_setup_msc": 1771150554509,
    "time_done": 1771150554,
    "time_done_msc": 1771150554509,
    "time_expiration": 0,
    "type": 0,
    "type_time": 0,
    "type_filling": 1,
    "state": 4,
    "magic": 0,
    "position_id": 42094820,
    "position_by_id": 0,
    "reason": 3,
    "volume_initial": 100.0,
    "volume_current": 0.0,
    "price_open": 0.3005,
    "sl": 0.28,
    "tp": 0.32,
    "price_current": 0.3005,
    "price_stoplimit": 0.0,
    "symbol": "ADAUSD",
    "comment": "Request executed",
    "external_id": ""
}
```

`state` 4 = filled, 2 = canceled, 5 = rejected, 6 = expired. `volume_current` 0 = fully filled.

**Deal object** (actual executed trades):

```json
{
    "ticket": 40536203,
    "order": 42094820,
    "time": 1771150554,
    "time_msc": 1771150554509,
    "type": 0,
    "entry": 0,
    "position_id": 42094820,
    "symbol": "ADAUSD",
    "volume": 100.0,
    "price": 0.3005,
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

`type`: 0 = buy, 1 = sell. `entry`: 0 = opening, 1 = closing. `profit` is 0 for entries, actual realized P&L for exits.

## Examples

```bash
# Check your balance
curl http://localhost:6542/account

# Grab some EURUSD H4 candles
curl "http://localhost:6542/symbols/EURUSD/rates?timeframe=H4&count=100"

# YOLO 1000 ADAUSD with SL and TP
curl -X POST http://localhost:6542/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ADAUSD", "type": "BUY", "volume": 1000, "sl": 0.25, "tp": 0.35}'

# Place a pending buy limit
curl -X POST http://localhost:6542/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ADAUSD", "type": "BUY_LIMIT", "volume": 1000, "price": 0.28, "sl": 0.25, "tp": 0.35}'

# Move your SL and TP
curl -X PUT http://localhost:6542/positions/12345 \
  -H "Content-Type: application/json" \
  -d '{"sl": 0.27, "tp": 0.36}'

# Close half
curl -X DELETE http://localhost:6542/positions/12345 \
  -H "Content-Type: application/json" \
  -d '{"volume": 500}'

# Close everything
curl -X DELETE http://localhost:6542/positions/12345

# Switch to demo account
curl -X POST http://localhost:6542/account/login/demo

# Get deal history for the last 24h
curl "http://localhost:6542/history/deals?from=$(date -d '1 day ago' +%s)&to=$(date +%s)"
```

## Technical Analysis

The API gives you raw market data — it doesn't do TA. If you need indicators, grab the candles from here and crunch them yourself. There's a full working example in `examples/python/` using [pandas-ta](https://github.com/twopirllc/pandas-ta) with ATR, RSI, MACD, Bollinger Bands, MFI, Stochastic, ADX, VWAP, and moving averages.

```bash
cd examples/python
pip install -r requirements.txt

# Default: EURUSD H4 200 candles
python ta.py

# Custom symbol/timeframe/count
python ta.py BTCUSD H1 100
python ta.py ADAUSD D1 200

# Custom API URL
MT5_API_URL=http://10.0.0.5:6542 python ta.py EURUSD D1

# Candlestick chart with TA overlays (1920x1080 PNG)
python chart.py ADAUSD
python chart.py BTCUSD H1 100
python chart.py EURUSD D1 200 -o eurusd.png
```

Check out `indicators.py` for the individual indicator functions and `signals.py` for signal detection. Use them as building blocks for your own shit.

## Make Targets

```
make up          Fire up the VM (downloads ISO if needed)
make down        Shut it down
make logs        Tail the logs
make status      Check VM and API status
make reinstall   Re-run MT5 installation on next boot
make clean       Nuke VM disk and state (keeps ISO)
make distclean   Nuke everything including ISO
```

## Ports

| Port | Service | Override |
|------|---------|----------|
| 8006 | noVNC (VM desktop) | `NOVNC_PORT=9006 make up` |
| 6542 | HTTP API | `API_PORT=7000 make up` |

## Project Structure

```
config/                 Your config shit
  account.json          Broker credentials (gitignored)
  terminal.json         Active broker/account selection (gitignored)
  account.json.example  Example credentials
  terminal.example.json Example terminal config
  requirements.txt      Python packages for the VM
  setup.bat             Custom boot commands

scripts/                Scripts that run inside the Windows VM
  install.bat           First-time setup (Python, MT5, firewall)
  start-mt5.bat         Runs on every boot (starts MT5 + API)

mt5api/                 Python HTTP API server
  handlers/             Route handlers
  config.py             Configuration
  mt5client.py          MT5 wrapper
  server.py             Flask routes

examples/               Usage examples
  python/               TA, charting, and API client modules

mt5installers/          Broker MT5 setup executables (gitignored)
data/                   Generated/volatile data (gitignored)
  win.iso               Windows ISO
  storage/              VM disk
  metatrader5/          Shared folder with VM
  oem/                  First-boot scripts
```

## Logs

Inside the VM's shared folder (`data/metatrader5/logs/`):

- `install.log` - MT5 installation progress
- `setup.log` - Boot-time setup output
- `pip.log` - Python package installation
- `api.log` - HTTP API server output

When shit breaks, check these first.

## License

WTFPL
