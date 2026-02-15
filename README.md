# mt5-httpapi

MetaTrader 5 running inside a real Windows VM (Docker + QEMU/KVM) with a REST API slapped on top for programmatic trading. No Wine bullshit, no janky workarounds - a legit Windows environment running the full MT5 terminal in portable mode.

Supports multiple brokers on the same VM. Throw in your broker's MT5 installers, set up your accounts, switch between them by editing `config/terminal.json` and restarting. Done.

## ⚠️ Disclaimer

This is a tool for automating trades. If you blow your account, that's on you. Use demo accounts first, test your shit, and don't come crying when your algo buys the top.

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

### Terminal

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/terminal` | Terminal info |
| POST | `/terminal/init` | Initialize MT5 connection |
| POST | `/terminal/shutdown` | Kill MT5 |

### Account

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/account` | Current account info |
| GET | `/account/list` | List saved accounts |
| POST | `/account/login` | Login with `{login, password, server}` |
| POST | `/account/login/:name` | Login by saved account name |

### Symbols

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/symbols` | List symbols (`?group=*USD*`) |
| GET | `/symbols/:symbol` | Symbol details |
| GET | `/symbols/:symbol/tick` | Latest tick |
| GET | `/symbols/:symbol/rates` | OHLCV candles (`?timeframe=H1&count=100`) |
| GET | `/symbols/:symbol/ticks` | Tick data (`?count=100`) |

Timeframes: `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1`

### Positions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/positions` | List open positions (`?symbol=`) |
| GET | `/positions/:ticket` | Get position |
| PUT | `/positions/:ticket` | Update SL/TP |
| DELETE | `/positions/:ticket` | Close position |

**PUT `/positions/:ticket`** - move your stop loss / take profit:

```json
{
    "sl": 0.27,
    "tp": 0.36
}
```

**DELETE `/positions/:ticket`** - close that shit:

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

**POST `/orders`** - send it:

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

**PUT `/orders/:ticket`** - change your mind on a pending order:

```json
{
    "price": 0.29,
    "sl": 0.26,
    "tp": 0.36,
    "type_time": "GTC"
}
```

All fields optional.

### Rates Response

What you get back from `GET /symbols/:symbol/rates`:

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

### Tick Response

What you get back from `GET /symbols/:symbol/ticks`:

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

### Trade Result

What comes back from POST/PUT/DELETE on orders and positions:

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

`retcode` 10009 = you're good. Anything else = something went wrong.

### History

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/history/orders` | Order history (`?from=TS&to=TS`) |
| GET | `/history/deals` | Deal history (`?from=TS&to=TS`) |

`from` and `to` are required, unix epoch seconds.

Deal object:

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
