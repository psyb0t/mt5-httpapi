---
title: "Trading and history"
description: "Open, modify and close positions and pending orders, decode MT5 trade results and retcodes, and read order and deal history."
nav_order: 2
parent: "REST API"
---

# Trading and history API — the real-money shit

Inspect positions and pending orders, place or change trades, decode broker results, and pull the order/deal history afterward.

> [!WARNING]
> These endpoints move real fucking money. Confirm the terminal, account, symbol, side, volume, price, SL, and TP before sending anything. There is no undo button after the broker fills it.

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

All fields optional. `volume` defaults to full position, `deviation` defaults to 20. The close always goes out with `IOC` filling; you can't pick another policy here.

PUT and DELETE on a position answer the trade result on success, 404 for an unknown ticket, and 500 `{"error": "Failed to update position: <broker comment>"}` (or `close`) for any retcode other than 10009. PUT needs a non-empty body (400 `Request body required`); a field you leave out keeps its current value.

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

`type`: 0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT, 4=BUY_STOP, 5=SELL_STOP, 6=BUY_STOP_LIMIT, 7=SELL_STOP_LIMIT. `state`: 1=placed, 2=canceled, 3=partial, 4=filled, 5=rejected, 6=expired.

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
  "client_order_id": "",
  "type_filling": "IOC",
  "type_time": "GTC"
}
```

Required: `symbol`, `type`, `volume`. Everything else is optional. `price` gets auto-filled for market orders. `position` and `position_by` (tickets) are passed through to MT5 too.

| Status | When |
| --- | --- |
| 201 | retcode 10009 (`DONE`) |
| 200 | Any other retcode (10008 placed, rejections...): the broker result as-is, check `retcode` |
| 400 | Missing `symbol`/`type`/`volume`, invalid `type`, bad `client_order_id` or a `comment` that differs from it, a bad `stoplimit` or `expiration`, no `stoplimit` on a stop-limit type, no `expiration` with `type_time` `SPECIFIED` or `SPECIFIED_DAY` |
| 404 | `Symbol X not found` |
| 500 | `Cannot get price for X` (no tick within ~2s on a market order) or `order_send failed: ...` |

### Safe retries with `client_order_id`

A timeout or a dropped connection on `POST /orders` leaves you not knowing whether the order went through. Send a `client_order_id` and a retry can't place it twice:

```json
{
  "symbol": "ADAUSD",
  "type": "BUY",
  "volume": 1000,
  "client_order_id": "rebalance-2026-10-21-01"
}
```

The id is 1 to 31 letters, digits or `._:-`, and it is stored as the order's `comment` (MT5 has one comment field), so leave `comment` out or send the same value. Before sending, the API looks for that id among the orders this terminal's API process accepted in the last 7 days, the pending orders, the open positions and the order history of the last 7 days. If it finds one, nothing is sent and the answer is 200:

```json
{
  "duplicate": true,
  "client_order_id": "rebalance-2026-10-21-01",
  "source": "position",
  "existing": { "ticket": 42094820, "symbol": "ADAUSD", "comment": "rebalance-2026-10-21-01" }
}
```

`source` is `recent_send`, `pending_order`, `position` or `history_order`, and `existing` is that object. A `recent_send` answer also repeats the original trade result's fields (`retcode`, `order`, `deal`, ...). A first send answers as usual, plus `"duplicate": false` and the `client_order_id`.

Use a new id for every order you mean to place, and reuse it only to retry that same order. An order the broker rejected is not remembered, so retrying it with the same id sends it again. Some brokers rewrite or drop order comments; then only a retry to the same API process within 7 days is caught, so keep the retry going to the same terminal.

Order types:

- Market: `BUY`, `SELL`
- Pending: `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`, `BUY_STOP_LIMIT`, `SELL_STOP_LIMIT`

Fill policies: `FOK`, `IOC` (default), `RETURN`. An unknown value is silently dropped.

Expiration types: `GTC` (default), `DAY`, `SPECIFIED`, `SPECIFIED_DAY`. An unknown value is silently dropped.

**Stop-limit and expiring orders.** `stoplimit` is the limit price of a `BUY_STOP_LIMIT` / `SELL_STOP_LIMIT` order: `price` is the stop trigger and `stoplimit` is the limit order it places. It is required for those two types and an optional non-negative number for the rest. `expiration` is integer unix seconds in real UTC, converted to the broker's clock with the terminal's `utc_offset` like every other time the API takes (see [broker time](rest-api.md#broker-time-vs-real-utc)). It is required when `type_time` is `SPECIFIED` or `SPECIFIED_DAY`. Send `expiration` with no `type_time` and the order gets `SPECIFIED`, because a broker ignores an expiry on a `GTC` or `DAY` order; if you name `GTC` or `DAY` yourself, the `expiration` is sent and ignored. A missing or bad `stoplimit` or `expiration` is a 400 and nothing reaches the broker.

```json
{
  "symbol": "ADAUSD",
  "type": "BUY_STOP_LIMIT",
  "volume": 1000,
  "price": 0.30,
  "stoplimit": 0.295,
  "type_time": "SPECIFIED",
  "expiration": 1771236954
}
```

**PUT `/orders/:ticket`** — change your mind on a pending order:

```json
{
  "price": 0.29,
  "sl": 0.26,
  "tp": 0.36,
  "type_time": "GTC"
}
```

All fields optional, but the body can't be empty (400 `Request body required`). A field you leave out keeps its current value, `stoplimit` and `expiration` included. They work as on `POST /orders`: `stoplimit` is a price and `expiration` is real UTC unix seconds. A new `expiration` with no `type_time` makes the order `SPECIFIED`, unless it is already `SPECIFIED` or `SPECIFIED_DAY`. Setting `type_time` to `SPECIFIED` or `SPECIFIED_DAY` on an order with no expiry needs an `expiration` too. A bad `stoplimit` or `expiration` is a 400. Answers the trade result on success, 404 for an unknown ticket, and 500 `{"error": "Failed to modify order: <broker comment>"}` for any retcode other than 10009. `DELETE /orders/:ticket` works the same way (`Failed to cancel order`).

## Trade Result

After you send, modify, cancel, or close something, MT5 throws this back:

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

`retcode` 10009 (`DONE`) = filled, you're good. 10008 (`PLACED`) = pending order accepted, also good. 10010 (`DONE_PARTIAL`) = partially filled. Anything else = the broker told you to fuck off.

## History

| Method | Endpoint          | Description                      |
| ------ | ----------------- | -------------------------------- |
| GET    | `/history/orders` | Order history (`?from=TS&to=TS`) |
| GET    | `/history/deals`  | Deal history (`?from=TS&to=TS`)  |

`from` and `to` are required, integer unix epoch seconds in real UTC (no `YYYY_MM_DD` form here, unlike rates). Missing or non-integer: 400.

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

`type`: 0 = buy, 1 = sell, 2 and up = balance, credit and other non-trade deals. `entry`: 0 = opening, 1 = closing, 2 = reversal (in/out), 3 = closed by an opposite position. `profit` is 0 for entries, actual realized P&L for exits.
