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

HEADER_AUTHORIZATION = "Authorization"
HEADER_REQUEST_ID = "X-Request-Id"
BEARER_PREFIX = "Bearer "

MCP_MOUNT_PATH = "/mcp"
MCP_STREAMABLE_HTTP_PATH = "/"

LOG_MAX_BYTES = 50_000_000
LOG_BACKUP_COUNT = 5

# Methods Flask reports on every rule but which are never part of the API
# surface a caller would drive.
SKIP_HTTP_METHODS = frozenset({"HEAD", "OPTIONS"})
