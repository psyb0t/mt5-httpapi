# Trading and history API

Read and change positions and orders, inspect broker results, and retrieve order/deal history.

> [!WARNING]
> Order and position mutations are live, irreversible trading actions. Confirm the selected terminal, symbol, side, volume, and price controls before sending them.

## Contents

- [Positions](#positions)
- [Orders](#orders)
- [Trade result](#trade-result)
- [History](#history)
- [REST routing and authentication](rest-api.md#api)

## Positions

| Method | Endpoint             | Description                      |
| ------ | -------------------- | -------------------------------- |
| GET    | `/positions`         | List open positions (`?symbol=`) |
| GET    | `/positions/:ticket` | Get position                     |
| PUT    | `/positions/:ticket` | Update SL/TP                     |
| DELETE | `/positions/:ticket` | Close position                   |

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

## Orders

| Method | Endpoint          | Description                      |
| ------ | ----------------- | -------------------------------- |
| GET    | `/orders`         | List pending orders (`?symbol=`) |
| POST   | `/orders`         | Place an order                   |
| GET    | `/orders/:ticket` | Get order                        |
| PUT    | `/orders/:ticket` | Modify order                     |
| DELETE | `/orders/:ticket` | Cancel order                     |

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

## Trade Result

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

## History

| Method | Endpoint          | Description                      |
| ------ | ----------------- | -------------------------------- |
| GET    | `/history/orders` | Order history (`?from=TS&to=TS`) |
| GET    | `/history/deals`  | Deal history (`?from=TS&to=TS`)  |

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

