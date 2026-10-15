"""Named values shared across the unifier package."""

DEFAULT_INSTANCE = "default"

ENV_MT5_HOST = "MT5_HOST"
ENV_API_TOKEN = "API_TOKEN"
ENV_LOG_LEVEL = "LOG_LEVEL"
ENV_LOG_FILE = "LOG_FILE"
ENV_LISTEN_HOST = "LISTEN_HOST"
ENV_LISTEN_PORT = "LISTEN_PORT"
ENV_REQUEST_TIMEOUT = "REQUEST_TIMEOUT"

DEFAULT_MT5_HOST = "mt5"
DEFAULT_LOG_LEVEL = "info"
DEFAULT_LOG_FILE = "/var/log/mcpunifier/app.log"
DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 6600

# Long enough for a Strategy Tester status poll or a wide rates window, short
# enough that a wedged terminal frees the caller instead of hanging the session.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0

# PUT /webrequest and POST /webrequest/apply take seconds in the VM
# (mt5api/chartctl/webrequest_apply.py) but up to 300s for a bare-metal
# terminal restart (mt5client.restart_terminal).
# nginx gives the unified /mcp/ route 300s (scripts/config_helper.py), so this
# stays just under it: the agent gets this service's timeout error, not an
# nginx 504.
WEBREQUEST_TIMEOUT_SECONDS = 290.0

HEADER_AUTHORIZATION = "Authorization"
HEADER_CONTENT_TYPE = "Content-Type"
HEADER_REQUEST_ID = "X-Request-Id"
BEARER_PREFIX = "Bearer "

MCP_MOUNT_PATH = "/mcp"
MCP_STREAMABLE_HTTP_PATH = "/"

LOG_MAX_BYTES = 50_000_000
LOG_BACKUP_COUNT = 5

# Methods Flask reports on every rule but which are never part of the API
# surface a caller would drive.
SKIP_HTTP_METHODS = frozenset({"HEAD", "OPTIONS"})

# The terminal process mode that never runs chartctl. mt5api treats every other
# mode, including a missing one, as live.
MODE_BACKTEST = "backtest"

# Feature tags on route-catalog entries that exist only when chartctl or the
# file API is on.
FEATURE_CHARTCTL = "chartctl"
FEATURE_FILES = "files"

# File API calls write and unzip on the VM's shared folder, which is slow; the
# same headroom as the WebRequest calls, under nginx's 300s on /mcp/.
FILES_TIMEOUT_SECONDS = 290.0

# get_file returns content inline in the JSON response; larger files are
# downloaded through REST instead.
FILES_MAX_INLINE_BYTES = 16 * 1024 * 1024
CONTENT_TYPE_JSON = "application/json"

CONTENT_TYPE_PNG = "image/png"
IMAGE_FORMAT_PNG = "png"

# Defaults of POST /charts/<chart_id>/screenshot (mt5api/chartctl/command.py).
SCREENSHOT_DEFAULT_WIDTH = 1280
SCREENSHOT_DEFAULT_HEIGHT = 720
