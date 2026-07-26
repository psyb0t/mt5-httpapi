"""MCP server for mt5-httpapi — the REST surface exposed over MCP.

mt5api is a Flask/WSGI app (served by waitress). This builds a FastMCP server
whose tools proxy IN-PROCESS to that same Flask app through a WSGI test client,
so every MCP call runs the exact same handler / auth / MT5 locking as a real
HTTP request — one code path, always in sync with the REST API:

  - ``ping``      — lock-free liveness (``GET /ping``)
  - ``endpoints`` — the Flask URL map (method + rule) so an agent can discover
                    every route instead of guessing paths
  - ``request``   — call ANY REST endpoint (method, path, query, body); the
                    single generic IO interface over the full API

The FastMCP app is ASGI; ``main.py`` bridges it into the WSGI stack via a2wsgi
(see the mount there). Mounted stateless with ``streamable_http_path = "/"`` so
``/mcp`` maps 1:1.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mt5api.config import API_TOKEN

logger = logging.getLogger(__name__)

_SKIP_METHODS = frozenset({"HEAD", "OPTIONS"})


def build_mcp_server() -> FastMCP:
    """Construct the FastMCP server mounted (via a2wsgi) under ``/mcp``."""
    mcp = FastMCP(
        name="mt5-httpapi",
        instructions=(
            "HTTP interface to a MetaTrader 5 terminal, exposed over MCP. Call "
            "`endpoints` to discover every REST route (account, symbols, live "
            "ticks and rates, orders, positions, history and Strategy-Tester "
            "backtests), then `request` to call any of them. Placing, modifying "
            "or cancelling orders and closing positions are real, irreversible "
            "actions on a live trading account with no client-side retry — only "
            "call those routes when the user explicitly asked for that specific "
            "action, and confirm the parameters first."
        ),
        stateless_http=True,
        json_response=True,
        # Headless self-hosted service fronted by the operator's own proxy/auth at
        # an arbitrary Host; the SDK's DNS-rebinding Host allowlist is a
        # browser-localhost mitigation that would 421 real-hostname deployments.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    mcp.settings.streamable_http_path = "/"

    @mcp.tool()
    async def ping() -> dict[str, Any]:
        """Lock-free liveness check (``GET /ping``)."""
        return await asyncio.to_thread(_call_wsgi, "GET", "/ping", None, None)

    @mcp.tool()
    async def endpoints() -> dict[str, Any]:
        """List every REST endpoint (method, path) from the Flask URL map — the
        catalog of routes ``request`` can call. Discover routes here rather than
        guessing paths."""
        # Late import: server.py imports THIS module at load time, so importing
        # the app at module scope would be circular.
        from mt5api.server import app

        found: list[dict[str, str]] = []
        for rule in app.url_map.iter_rules():
            for method in sorted((rule.methods or set()) - _SKIP_METHODS):
                found.append({"method": method, "path": str(rule)})
        found.sort(key=lambda entry: (entry["path"], entry["method"]))
        return {"endpoints": found}

    @mcp.tool()
    async def request(
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call any mt5-httpapi REST endpoint and return its JSON response.

        ``method``: GET / POST / PUT / DELETE / etc. ``path``: a full route from
        ``endpoints``, e.g. ``/account`` or ``/orders``. ``query``: URL query
        params. ``body``: JSON body for POST / PUT. The call runs the exact same
        handler, auth and MT5 locking as a real HTTP request (in-process).

        Trade / order / position mutations are irreversible and hit a LIVE
        account with no client-side retry — only call them when the user asked
        for that exact action, and confirm the parameters first.
        """
        verb = method.upper().strip()
        target = path if path.startswith("/") else "/" + path
        # Log the route only — never the body/query, which can carry order params.
        logger.info("mcp proxy request", extra={"http_method": verb, "path": target})
        # The WSGI call is blocking (and may take the process-wide MT5 lock), so
        # run it off the event loop.
        return await asyncio.to_thread(_call_wsgi, verb, target, query, body)

    return mcp


def _call_wsgi(
    method: str,
    path: str,
    query: dict[str, Any] | None,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    """Dispatch one request through the Flask app's WSGI test client in-process,
    injecting the configured bearer token so it clears the app's own auth."""
    from mt5api.server import app

    headers: dict[str, str] = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    client = app.test_client()
    resp = client.open(
        path,
        method=method,
        query_string=query or None,
        json=body if body is not None else None,
        headers=headers,
    )
    return {"status": resp.status_code, "body": _decode(resp)}


def _decode(resp: Any) -> Any:
    """Return the response as parsed JSON, falling back to raw text for a
    non-JSON body (e.g. a Werkzeug HTML error page). ``get_json`` returns None
    for a non-JSON content type rather than raising, so a bare error page would
    otherwise surface as ``null`` instead of its text."""
    data = resp.get_json(silent=True)
    if data is not None:
        return data
    return {"raw": resp.get_data(as_text=True)}
