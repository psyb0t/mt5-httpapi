"""Named values shared across the signals package."""

import re

DEFAULT_INSTANCE = "default"

ENV_MT5_URL = "MT5_URL"
ENV_API_TOKEN = "API_TOKEN"
ENV_TELETHON_URL = "TELETHON_URL"
ENV_TELETHON_AUTH_KEY = "TELETHON_AUTH_KEY"
ENV_STATE_PATH = "SIGNALS_STATE_PATH"
ENV_HEARTBEAT_PATH = "SIGNALS_HEARTBEAT_PATH"
ENV_LOG_LEVEL = "LOG_LEVEL"

# nginx routes every terminal, on every VM, under /<broker>/<account>/<instance>/,
# so going through it keeps multi-VM setups working without a host per VM.
DEFAULT_MT5_URL = "http://nginx"
DEFAULT_TELETHON_URL = "http://telethon-plus:8080"
DEFAULT_STATE_PATH = "/data/signals.sqlite"
DEFAULT_HEARTBEAT_PATH = "/tmp/signals-heartbeat"
DEFAULT_LOG_LEVEL = "info"

# An order send waits for the broker; nginx gives a terminal route 60 seconds.
TERMINAL_TIMEOUT_SECONDS = 50.0
TELETHON_TIMEOUT_SECONDS = 30.0

SOURCE_TYPE_TELEGRAM = "telegram"
SOURCE_TYPE_WEBSOCKET = "websocket"
SOURCE_TYPES = frozenset({SOURCE_TYPE_TELEGRAM, SOURCE_TYPE_WEBSOCKET})

# client_order_id is "<source>:<message id>:<leg>" and mt5-httpapi allows at
# most 31 characters from [A-Za-z0-9._:-]. A 16-character source name, a
# 10-character id and a one-digit leg come to 29.
SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")
MESSAGE_ID_HASH_CHARS = 10
MAX_LEGS = 9
CLIENT_ORDER_ID_RE = re.compile(r"^([A-Za-z0-9._-]{1,16}):([A-Za-z0-9]{1,10}):(\d)$")

# A telegram channel that posts every half hour needs no poll every minute,
# but a poll more often than this only burns telethon-plus's read bucket.
DEFAULT_POLL_SECONDS = 60.0
MIN_POLL_SECONDS = 10.0
# Messages read per catch-up poll; a channel rarely posts more than this
# between two polls, and the websocket delivers the rest live.
POLL_MESSAGES = 20

# A signal older than this when it is first seen (a catch-up after downtime)
# is not traded: the price has moved on since the channel posted it.
DEFAULT_MAX_SIGNAL_AGE_SECONDS = 300.0
# max_signal_age is written as whole units, largest first: "10m", "10m30s",
# "1h35m40s", "1d". A bare number is refused because nothing says whether it
# means seconds or minutes.
DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
DURATION_UNIT_SECONDS = (86400, 3600, 60, 1)

# A scheduled VM reboot leaves every terminal unreachable for about four
# minutes, and just before one the API refuses writes with 503
# REBOOT_PENDING. A call that fails that way is retried at this interval for
# as long as the signal is younger than its max_signal_age, so a signal
# posted during a reboot is still traded once the terminal is back.
TERMINAL_RETRY_SECONDS = 15.0
TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})

DEFAULT_DEVIATION = 20

SIZING_FIXED = "fixed"
SIZING_MULTIPLIER = "multiplier"
SIZING_RISK_PERCENT = "risk_percent"
SIZING_MODES = frozenset({SIZING_FIXED, SIZING_MULTIPLIER, SIZING_RISK_PERCENT})

NETTING_TP_FIRST = "first"
NETTING_TP_LAST = "last"
NETTING_TP_CHOICES = frozenset({NETTING_TP_FIRST, NETTING_TP_LAST})

SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDES = frozenset({SIDE_BUY, SIDE_SELL})

# ACCOUNT_MARGIN_MODE_RETAIL_HEDGING. 0 (netting) and 1 (exchange) keep one
# position per symbol, so TP legs cannot be separate positions there.
MARGIN_MODE_HEDGING = 2

# POST /orders answers 201 only for TRADE_RETCODE_DONE.
HTTP_CREATED = 201
HTTP_NOT_FOUND = 404

RECONNECT_MIN_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 60.0
HEARTBEAT_SECONDS = 30.0
# Seen-message rows are kept long enough to cover any catch-up window.
SEEN_RETENTION_SECONDS = 30 * 24 * 3600

HEADER_AUTHORIZATION = "Authorization"
BEARER_PREFIX = "Bearer "

# Telegram ids as Telethon events report them ("marked"): channels and
# supergroups are -100<id>, basic groups -<id>, users <id>. Message listings
# and entity lookups report the bare <id>.
MARKED_CHANNEL_OFFSET = 1_000_000_000_000
