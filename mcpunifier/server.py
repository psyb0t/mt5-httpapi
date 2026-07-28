"""FastAPI app hosting the unified MCP endpoint.

The MCP app is ASGI and mounts natively here — no WSGI bridge, unlike the
per-terminal server that lives inside the Flask app.
"""

import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from mcpunifier.client import TerminalClient
from mcpunifier.config import Settings, load_settings
from mcpunifier.constants import (
    BEARER_PREFIX,
    HEADER_AUTHORIZATION,
    HEADER_REQUEST_ID,
    MCP_MOUNT_PATH,
)
from mcpunifier.logging import configure_logging, reset_scope, with_scope
from mcpunifier.mcp_server import build_mcp_server

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_MAX_REQUEST_ID_CHARS = 64
_HEALTH_PATH = "/health"


def _is_shape_valid(value: str) -> bool:
    return (
        len(value) <= _MAX_REQUEST_ID_CHARS
        and "\n" not in value
        and bool(_UUID_RE.match(value))
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Seed the log scope with a request id and echo it back."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(HEADER_REQUEST_ID.lower(), "")
        request_id = incoming if _is_shape_valid(incoming) else str(uuid.uuid4())
        token = with_scope(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            reset_scope(token)
        response.headers[HEADER_REQUEST_ID] = request_id
        return response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require the API bearer on everything except the health probe.

    The unified endpoint reaches every terminal, so it is gated by the same
    token the terminals themselves use. An empty token disables auth, matching
    the REST side's behaviour rather than inventing a second rule.
    """

    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if not self._token or request.url.path == _HEALTH_PATH:
            return await call_next(request)

        presented = request.headers.get(HEADER_AUTHORIZATION.lower(), "")
        if presented != f"{BEARER_PREFIX}{self._token}":
            logger.warning(
                "rejecting unauthenticated request",
                extra={"path": request.url.path, "reason": "bad_or_missing_bearer"},
            )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        return await call_next(request)


class McpTrailingSlashMiddleware:
    """Rewrite bare ``/mcp`` to ``/mcp/`` before routing.

    Starlette's Mount serves at ``/mcp/*`` but 307-redirects the bare form, and
    some MCP clients do not follow that redirect on a POST.
    """

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == MCP_MOUNT_PATH:
            scope = dict(scope)
            scope["path"] = f"{MCP_MOUNT_PATH}/"
            scope["raw_path"] = f"{MCP_MOUNT_PATH}/".encode()
        await self._app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app with the unified MCP server mounted at /mcp."""
    resolved = settings if settings is not None else load_settings()
    client = TerminalClient(resolved)
    mcp_server = build_mcp_server(resolved, client)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting unifier",
            extra={
                "terminals": resolved.terminal_keys(),
                "mt5_host": resolved.mt5_host,
            },
        )
        # The streamable-HTTP transport needs its session manager running for
        # the whole app lifetime.
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                await client.aclose()
                logger.info("unifier stopped")

    app = FastAPI(
        title="mt5-httpapi-unifier",
        description=(
            "One MCP endpoint across every configured MT5 terminal. "
            "Tools take broker/account to pick which terminal to act on."
        ),
        lifespan=lifespan,
    )

    @app.get(_HEALTH_PATH)
    async def health() -> dict[str, object]:
        """Liveness for the unifier itself.

        Deliberately does NOT probe the terminals: this service is up whenever
        it can route, and a down terminal is a per-call outcome rather than a
        reason to report the whole service unhealthy.
        """
        return {"status": "ok", "terminals": resolved.terminal_keys()}

    app.add_middleware(BearerAuthMiddleware, token=resolved.api_token)
    app.add_middleware(RequestIDMiddleware)
    app.mount(MCP_MOUNT_PATH, mcp_server.streamable_http_app())
    return app


def build() -> FastAPI:
    """Entry point used by the container: configure logging, then build."""
    settings = load_settings()
    configure_logging(settings.log_level, settings.log_file)
    app = create_app(settings)
    return McpTrailingSlashMiddleware(app)  # type: ignore[return-value]
