---
title: "Signal copier"
description: "Copy trade signals from Telegram channels or a websocket feed onto MT5 accounts, with regex parsers, per-account sizing, TP legs and limits."
nav_order: 7
---

# Signal copier: trade signals from Telegram and other feeds

The signal copier reads trade signals from Telegram channels and groups, or from any websocket feed, and places them as orders on one or more of the configured terminals. Each account gets its own sizing, so a channel's "1 lot" can be 0.1 lots on one account and half a percent of equity on another. A signal with several take profits opens one position per TP, and limits per symbol and per source drop new signals while enough are already open.

It runs as its own container, `signals`, beside the stack and places orders through nginx with the API token, like any other client of the API. Telegram is read through a second container, `telethon-plus` ([docker-telethon-plus](https://github.com/psyb0t/docker-telethon-plus)), which logs in as a Telegram account. Both are off unless `config.yaml` turns them on.

The copier places real orders on every target account. Start each new target with `dry_run: true` and read the log until the planned orders are the ones you want.

## Contents

- [Turning it on](#turning-it-on)
- [Logging in to Telegram](#logging-in-to-telegram)
- [The rules file](#the-rules-file)
- [Sources](#sources)
- [Parsers](#parsers)
- [Targets and sizing](#targets-and-sizing)
- [Take profit legs](#take-profit-legs)
- [Limits](#limits)
- [When a signal is skipped](#when-a-signal-is-skipped)
- [Duplicates, restarts and missed messages](#duplicates-restarts-and-missed-messages)
- [Logs](#logs)
- [Not handled yet](#not-handled-yet)

## Turning it on

1. In `config/config.yaml`, set `signals.enabled: true` (`signals: true` is shorthand for it). For Telegram sources, also fill in the `telegram:` block (next section).
2. Copy [`config/signals.yml.example`](https://github.com/psyb0t/mt5-httpapi/blob/master/config/signals.yml.example) to `config/signals.yml` and edit it. It is gitignored like `config.yaml`.
3. Run `make up`.

```yaml
# config/config.yaml
signals:
  enabled: true

telegram:
  api_id: "123456"
  api_hash: "your-api-hash"
  session: "1Aa...from make telegram-login..."
  auth_key: "any-random-string"
```

`run.sh` reads these on every `make up` and writes `COMPOSE_PROFILES` to `.env`: `signals` when the copier is enabled, plus `telegram` when `signals.yml` has a `type: telegram` source. Compose reads `.env` on every command, so `make down` and `make logs` see the same services. With a telegram source, `run.sh` also writes `telegram.api_id`, `api_hash`, `session` and `auth_key` into `.env` as `TELEGRAM_API_ID` and so on, for the `telethon-plus` service; a value containing `'` stops `run.sh`, and a change to these values needs `make up`. With `signals.enabled: true` and no `config/signals.yml`, `run.sh` stops with an error instead of starting a copier with nothing to do.

A `docker-compose.yml` generated before this feature does not have the two services. Copy the `signals` and `telethon-plus` services and the `signals-state` and `telethon-cache` volumes from `docker-compose.yml.example`, or regenerate the file from `docker-compose.yml.j2` when you use `vms.yaml`.

The copier reads `config.yaml` (for `api_token`, the terminal list and `telegram.auth_key`) and `signals.yml` once at startup. After editing either, run `docker compose restart signals`. A mistake in `signals.yml` (an unknown parser, a regex without its named groups, a target naming a terminal `config.yaml` does not have, two sources with one magic number) stops the copier at startup with a log line naming the setting; it never starts half-configured.

## Logging in to Telegram

The copier reads channels as a Telegram user account, not as a bot, so it can read any channel or group that account has joined.

1. Create an app at <https://my.telegram.org/apps> and put its `api_id` and `api_hash` into the `telegram:` block of `config.yaml`.
2. Run `make telegram-login`. It asks for the phone number, the login code Telegram sends, and the two-step verification password if the account has one, then prints a session string.
3. Put that string into `telegram.session` and run `make up`.

The session string is full access to the Telegram account: keep it as private as the broker passwords next to it. Like them, it is in every copy of `config.yaml`, including the one `run.sh` puts in the VM's shared directory, and in `.env`. `telethon-plus` runs with `TELETHON_READ_ONLY=true`, so nothing in this stack can send a message, join a chat or change anything on the account, and it publishes no port. `auth_key` guards its API inside the compose network; set it to any random string (`openssl rand -hex 32`).

The account must have joined every channel or group a source reads: Telegram sends live updates only for chats the account is in.

## The rules file

`config/signals.yml` has five parts:

| Key | What it holds |
| --- | --- |
| `sources` | Where messages come from. |
| `parsers` | The regexes that turn a message into a signal. |
| `symbols` | The channel's symbol names mapped to one common name. |
| `targets` | Which account copies which source, and how. |
| `dry_run`, `max_signal_age_seconds` | `dry_run: true` sends no order anywhere and only logs them. A message first seen more than `max_signal_age_seconds` (default 300) after it was posted is not traded. |

`config/signals.yml.example` shows every setting with a comment.

## Sources

Every source has a name, a `type`, a `parser` and a `magic` number. All types hand the same thing to the rest of the copier: a message with an id, a time and its text. Parsing, sizing and limits work the same whatever the type.

```yaml
sources:
  goldsignals:
    type: telegram
    chat: "@some_signal_channel"
    parser: classic
    magic: 710001
    poll_seconds: 60

  mybot:
    type: websocket
    url: wss://signals.example.invalid/feed
    headers:
      Authorization: "Bearer ${MYBOT_TOKEN}"
    text_field: message
    id_field: id
    parser: classic
    magic: 710002
```

- **Name:** 1 to 16 letters, digits or `._-`. It becomes part of every order's `client_order_id`, which mt5-httpapi limits to 31 characters.
- **`magic`:** a whole number above 0, different for every source. Every order from the source carries it, and the limits count by it. Pick numbers no EA on the same accounts uses.
- **`type: telegram`:** `chat` is a username, a t.me link or a numeric id. New messages arrive live through telethon-plus. `poll_seconds` (default 60, minimum 10) is how often the copier also reads the chat's latest 20 messages, which catches anything the live feed missed. A channel that posts every half hour does fine with `poll_seconds: 600`. A shorter interval spends more of telethon-plus's read budget, which exists to keep Telegram from limiting the account.
- **`type: websocket`:** `url` is a `ws://` or `wss://` address. Each frame is one message: the frame text itself, or, with `text_field`, a JSON object whose `text_field` holds the text. `id_field` names the field with the message id; without it each frame gets its own id. `id_field` is read only together with `text_field`; plain text frames always get a generated id. An id that is not 1 to 10 letters or digits is hashed to fit the `client_order_id`. A frame's time is when it arrived, so `max_signal_age_seconds` never skips a websocket message. `headers` are sent on connect. `${NAME}` in `url` or `headers` is replaced with the environment variable of that name, which the copier must have (add it to the `signals` service's `environment`).

## Parsers

A parser is a set of Python regular expressions with named groups. A message is a signal when `open` matches it with a side the parser knows.

```yaml
parsers:
  classic:
    open: '(?i)(?P<symbol>XAUUSD|GOLD|EURUSD)\s+(?P<side>buy|sell|long|short)(?:\s+(?:now|limit|stop))?\s*@?\s*(?P<entry>\d+(?:[.,]\d+)?)?(?:\s*[-/]\s*(?P<entry2>\d+(?:[.,]\d+)?))?'
    sl: '(?i)\bsl(?P<n>\d)?\b\s*[:=@-]?\s*(?P<price>\d+(?:[.,]\d+)?)'
    tp: '(?i)\btp(?P<n>\d)?\b\s*[:=@-]?\s*(?P<price>\d+(?:[.,]\d+)?)'
    lots: '(?i)(?P<lots>\d+(?:[.,]\d+)?)\s*lots?\b'
    side_map:
      long: buy
      short: sell
```

| Regex | Groups | What it reads |
| --- | --- | --- |
| `open` (required) | `side`, `symbol` (required); `entry`, `entry2`, `lots` | Whether the message is a signal, its direction and symbol, an entry price or an entry range, and the lots when they are on the same line. |
| `sl` | `price` (required); `n` | Every stop loss in the message. |
| `tp` | `price` (required); `n` | Every take profit in the message. |
| `lots` | `lots` | The lots, when `open` does not capture them. |

`sl` and `tp` are applied as often as they match. A match with an `n` group takes that number (`SL2`, `TP3`). A match without one takes the next free number in the order it appears, so `TP 2360` then `TP 2370` read as TP1 and TP2. Keep `n` glued to the keyword and followed by a word boundary, as above (`\bsl(?P<n>\d)?\b`): with a space allowed between them, `SL 2350` reads as SL number 2 at price 350.

`side_map` maps words to `buy` or `sell`, matched without case; `buy` and `sell` are always known. Prices may use a comma as the decimal separator. Symbols are upper-cased. A message that does not match `open` is recorded as seen and ignored.

## Targets and sizing

A target is one terminal copying one source. A source can have any number of targets, and every target trades every signal of its source on its own.

```yaml
targets:
  - source: goldsignals
    broker: ftmo
    account: main
    # instance: default
    sizing: {mode: multiplier, factor: 0.1, default_lots: 0.01}
    max_lots: 1.0
    tp_split: [50, 30, 20]
    require_sl: true
    max_entry_slippage_points: 50
    symbol_suffix: ""
    symbols: {XAUUSD: GOLD}
    netting_tp: first
    deviation: 20
    # type_filling: FOK
    limits:
      max_open_per_symbol: 2
      max_open_per_source: 3
      max_open_per_source_symbol: 1
    dry_run: true
```

`broker`, `account` and `instance` (default `default`) name a terminal from `config.yaml`. The orders go to `/<broker>/<account>/<instance>/orders` through nginx, so terminals on any VM work.

Sizing decides the signal's whole volume before it is split into legs:

| `mode` | Settings | Volume |
| --- | --- | --- |
| `fixed` | `lots` | Always `lots`. |
| `multiplier` | `factor`, `default_lots` (optional) | The signal's lots times `factor`. A signal without lots uses `default_lots` times `factor`, or is skipped when `default_lots` is not set. |
| `risk_percent` | `percent` | The volume that loses `percent` of the account's equity if the price reaches the signal's farthest SL, from the symbol's tick size and tick value. `percent` is at most 100. Needs `require_sl: true`. |

`max_lots` caps the whole volume. The legs never add up to more than the volume rounded down to the symbol's `volume_step`, and each is capped at its `volume_max`, so rounding never adds risk.

The symbol is the channel's name mapped through the top-level `symbols` map, then through the target's own `symbols` map; a name the target's map does not have gets `symbol_suffix` appended. A symbol the terminal does not know skips the signal on that target.

The rest:

- `require_sl` (default `true`): skip signals without an SL.
- `max_entry_slippage_points`: skip when the price is more than this many points worse than the signal's entry (above it for a buy, below it for a sell). With an entry range the far edge counts. Signals without an entry are not checked.
- `deviation` (default 20): the market order's maximum price deviation in points.
- `type_filling`: `FOK`, `IOC` or `RETURN`, for brokers that refuse the API's default `IOC`. It is not checked at startup; the API ignores any other value.
- `dry_run`: log this target's orders without sending them.

## Take profit legs

On a hedging account a signal with several TPs opens one position per TP. Leg *n* gets TP*n* and SL*n*, or SL1 when the signal has fewer SLs than TPs. `tp_split` gives each leg its share of the volume in percent; without it the legs are equal, and with it only as many TPs as it has entries are used (at most 9). The shares are weights: with fewer TPs than entries, only the first entries are used and rescaled to the whole volume, so `[50, 30, 20]` on a two TP signal gives 62.5% and 37.5%. Dropped legs rescale the same way. The volume is rounded down to the symbol's `volume_step` once and handed out in whole steps, so the legs add up to it: 0.03 lots split `[34, 33, 33]` is three legs of 0.01. When a leg would fall below the symbol's `volume_min`, the far legs are dropped and their volume goes to the rest; a signal whose whole volume is below `volume_min` is skipped.

A netting account (the terminal's `GET /account` answers `margin_mode` 0 or 1) holds one position per symbol, so the whole volume goes into one order with TP1 (`netting_tp: first`, the default) or the last TP (`netting_tp: last`).

Every leg is a market order with `magic` set to the source's magic and `client_order_id` set to `<source>:<message id>:<leg>`.

## Limits

The limits drop a new signal on a target while that account already has enough open:

| Limit | Counts |
| --- | --- |
| `max_open_per_symbol` | Open signals on the signal's symbol, from every source of this file. With `count_manual: true`, every position and pending order on the symbol counts, including trades opened by hand or by an EA. |
| `max_open_per_source` | Open signals from this source, on any symbol. |
| `max_open_per_source_symbol` | Open signals from this source on the signal's symbol. |

A signal counts as open while any of its legs is an open position or a pending order; the legs of one signal count once. The counts come from the terminal (`GET /positions` and `GET /orders`) right before each decision, not from the copier's memory, so they are right after a restart and after you close trades by hand. Signals for the same account are handled one at a time, so two arriving together cannot both pass a limit of 1. A limit left out does not apply.

Legs are told apart by their comment. A broker that rewrites order comments leaves only the magic number, and then each leg counts as one open signal.

## When a signal is skipped

A skip applies to one target only; the source's other targets still trade the signal. Each is logged as `signal skipped` with a `reason`:

| `reason` | Why |
| --- | --- |
| `unknown_symbol` | The terminal has no symbol by that name. |
| `no_sl` | The signal has no SL and the target has `require_sl: true`. |
| `price_past_sl` | The price is already at or past an SL. |
| `price_past_tp` | The price is already at or past TP1. |
| `entry_slippage` | The price is further from the entry than `max_entry_slippage_points`. |
| `max_open_per_symbol`, `max_open_per_source`, `max_open_per_source_symbol` | That limit is full. |
| `no_lots_in_signal` | `multiplier` sizing, no lots in the signal and no `default_lots`. |
| `volume_below_minimum` | The sized volume is below the symbol's `volume_min`. |
| `symbol_info_incomplete` | The terminal's symbol info lacks a field the sizing needs. |
| `sl_at_entry` | `risk_percent` sizing with every SL at the entry price. `price_past_sl` normally catches this first. |

A terminal that does not answer, or answers with an error, while the copier is still checking a signal (before any leg is sent) logs `terminal call failed, signal not traded`. Once the legs are being sent, each leg goes out on its own: when the call for one leg fails, that leg logs `terminal call failed, leg not sent` with its `leg`, `client_order_id`, `reason` (`TerminalUnreachable` or `TerminalRejected`) and `error`, and the remaining legs are still sent. A failed leg is not retried, so a signal can end up with fewer positions than legs planned; the log line names the missing one. An order the broker refuses logs `broker refused the order` with the `retcode` and the broker's comment, and does not stop the other legs either.

A message is recorded as seen even when every target skipped it, failed or ran dry, so it is never retried.

## Duplicates, restarts and missed messages

- Every handled message is recorded in a SQLite file on the `signals-state` volume, so a message that arrives twice (live and in a poll, or again after a restart) trades once. Records are kept for 30 days.
- Each leg's `client_order_id` makes the terminal itself refuse a second copy of the same order, so even a crash between placing an order and recording the message does not double it. See [Safe retries with client_order_id](trading-and-history.md#safe-retries-with-client_order_id).
- The first time a Telegram source is read, its recent messages are recorded as seen and not traded, so adding a channel never fires its backlog.
- Telegram messages posted while the copier or the live feed was down are picked up by the poll after it reconnects, and traded if they are younger than `max_signal_age_seconds`. A websocket source has no history, so frames sent while it is disconnected are lost.

## Logs

```bash
docker compose logs -f signals
```

Each line is JSON with `msg`, the `source`, `message_id` and `terminal` it concerns, and for orders the `leg`, `volume`, `sl`, `tp` and `client_order_id`. Values whose key looks like a secret are redacted. With `LOG_LEVEL: debug` in the `signals` service's `environment` in `docker-compose.yml`, the log also shows every message that is not a signal and every terminal call.

Other `msg` values:

| `msg` | When |
| --- | --- |
| `invalid configuration, not starting` | `signals.yml` or `config.yaml` has a mistake; `reason` names it. |
| `first start, existing messages recorded as seen and not traded` | A Telegram source's first read. |
| `message too old to trade` | The message is older than `max_signal_age_seconds`. |
| `signal parsed` | A message matched `open`. |
| `legs planned` | Volume and legs for one target, with `tps_without_leg` for TPs that got no leg. |
| `dry run, order not sent` | A leg under `dry_run`. |
| `order placed` | The broker filled the leg; carries the `ticket`. |
| `order already placed` | The terminal found an order with this `client_order_id`. |
| `terminal call failed, leg not sent` | The terminal did not answer, or answered with an error, for one leg's order; the other legs are still sent. |
| `signal failed on this target` | An unexpected error on one target; the others still trade. |

The `signals` service also reads these environment variables:

| Variable | Default | What it sets |
| --- | --- | --- |
| `API_TOKEN` | `api_token` from `config.yaml` | Bearer token for the terminals |
| `MT5_URL` | `http://nginx` | Where the terminals are reached |
| `TELETHON_URL` | `http://telethon-plus:8080` | The telethon-plus API |
| `TELETHON_AUTH_KEY` | `telegram.auth_key` from `config.yaml` | Token for telethon-plus |
| `SIGNALS_STATE_PATH` | `/data/signals.sqlite` | The seen-message database |
| `SIGNALS_HEARTBEAT_PATH` | `/tmp/signals-heartbeat` | File touched every 30 seconds; the compose healthcheck fails when it is older than 120 seconds |
| `LOG_LEVEL` | `info` | Log level |

## Not handled yet

- Follow-up messages: "close now", "move SL to breakeven", "cancel", "TP1 hit". Trades opened from a signal are managed by hand, or by an EA, after they open.
- Edits and deletions of a signal message after it was traded.
- Pending orders. Every signal is traded at market, including "BUY LIMIT" signals, as long as the entry and slippage checks allow it.
- Partial closes on netting accounts, where all TPs except the chosen one are ignored.
