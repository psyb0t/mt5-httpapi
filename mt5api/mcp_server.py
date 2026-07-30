"""MCP server for mt5-httpapi — the REST surface exposed over MCP.

mt5api is a Flask/WSGI app (served by waitress). This builds a FastMCP server
whose tools proxy IN-PROCESS to that same Flask app through a WSGI test client,
so every MCP call runs the exact same handler / auth / MT5 locking as a real
HTTP request — one code path, always in sync with the REST API.

Tool families:
  - Health/terminal — ``ping``, ``get_terminal``, ``terminal_control``
  - Account          — ``get_account``
  - Market data      — ``list_symbols``, ``get_symbol``, ``get_tick``,
                        ``get_rates``, ``get_ticks``, ``get_rates_ta``
  - Positions        — ``list_positions``, ``get_position``,
                        ``modify_position``, ``close_position``
  - Orders           — ``list_orders``, ``get_order``, ``create_order``,
                        ``modify_order``, ``cancel_order``
  - History          — ``get_history_orders``, ``get_history_deals``
  - Backtest         — ``get_backtest`` (poll; new runs are multipart, submit via REST)
  - Escape hatches   — ``request`` (JSON routes) and ``endpoints`` (route catalog)

Each tool is a thin typed wrapper: it maps friendly params to
(method, path, query, body) and calls the same in-process WSGI helper the
generic ``request`` tool uses — no handler logic is duplicated, and every
call passes through the same auth/locking as a real HTTP request.

The FastMCP app is ASGI; ``main.py`` bridges it into the WSGI stack via a2wsgi
(see the mount there). Mounted stateless with ``streamable_http_path = "/"`` so
``/mcp`` maps 1:1.

Live-account safety note: placing/modifying/cancelling orders and
modifying/closing positions are real, irreversible actions on a live trading
account with no client-side retry — only call those tools when the user
explicitly asked for that specific action, and confirm the parameters first.
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

# Live-account safety note appended to every destructive tool's docstring.
_LIVE_NOTE = (
    "This is an irreversible action on a live trading account — only call it "
    "when the user explicitly asked for that specific action."
)


def build_mcp_server() -> FastMCP:
    """Construct the FastMCP server mounted (via a2wsgi) under ``/mcp``."""
    mcp = FastMCP(
        name="mt5-httpapi",
        instructions=(
            "HTTP interface to a MetaTrader 5 terminal, exposed over MCP as "
            "dedicated typed tools grouped by family: health/terminal "
            "(ping, get_terminal, terminal_control), account (get_account), "
            "market data (list_symbols, get_symbol, get_tick, get_rates, "
            "get_ticks, get_rates_ta), positions (list_positions, "
            "get_position, modify_position, close_position), orders "
            "(list_orders, get_order, create_order, modify_order, "
            "cancel_order), history (get_history_orders, get_history_deals) "
            "and backtests (get_backtest — polling; new runs are multipart, "
            "submit via the REST API). `request` and "
            "`endpoints` remain as an escape hatch for routes without a "
            "dedicated tool. Placing, modifying or cancelling orders and "
            "modifying or closing positions are real, irreversible actions "
            "on a live trading account with no client-side retry — only "
            "call those tools when the user explicitly asked for that "
            "specific action, and confirm the parameters first."
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

    # ── Health / terminal ────────────────────────────────────────────

    @mcp.tool()
    async def ping() -> dict[str, Any]:
        """Lock-free liveness check (``GET /ping``)."""
        return await _call("GET", "/ping")

    @mcp.tool()
    async def get_terminal() -> dict[str, Any]:
        """Get MT5 terminal info: build, broker/connection state, trade
        permissions (``GET /terminal``)."""
        return await _call("GET", "/terminal")

    @mcp.tool()
    async def terminal_control(action: str) -> dict[str, Any]:
        """Control the MT5 terminal connection: ``action`` is one of
        "init", "shutdown", "restart" (``POST /terminal/{action}``).

        ``shutdown`` disconnects this API process from the MT5 SDK while
        leaving terminal64.exe running. ``restart`` kills and relaunches this
        terminal process. Only call either on explicit user request.
        """
        return await _call("POST", f"/terminal/{action}")

    # ── Account ──────────────────────────────────────────────────────

    @mcp.tool()
    async def get_account() -> dict[str, Any]:
        """Get the logged-in account's balance, equity, margin and trading
        permissions (``GET /account``)."""
        return await _call("GET", "/account")

    # ── Market data ──────────────────────────────────────────────────

    @mcp.tool()
    async def list_symbols(group: str = "") -> dict[str, Any]:
        """List tradable symbol names, optionally filtered by a glob-style
        ``group`` pattern, e.g. ``"*USD*"`` (``GET /symbols``)."""
        query = {"group": group} if group else None
        return await _call("GET", "/symbols", query=query)

    @mcp.tool()
    async def get_symbol(symbol: str) -> dict[str, Any]:
        """Get full specification for one symbol: digits, point, contract
        size, margin/volume limits, etc. (``GET /symbols/{symbol}``)."""
        return await _call("GET", f"/symbols/{symbol}")

    @mcp.tool()
    async def get_tick(symbol: str) -> dict[str, Any]:
        """Get the latest bid/ask/last tick for one symbol
        (``GET /symbols/{symbol}/tick``)."""
        return await _call("GET", f"/symbols/{symbol}/tick")

    @mcp.tool()
    async def get_rates(
        symbol: str,
        timeframe: str,
        count: int = 0,
        from_: str = "",
        to: str = "",
    ) -> dict[str, Any]:
        """Get OHLCV bars for one symbol/timeframe (``GET
        /symbols/{symbol}/rates``).

        ``timeframe``: one of M1/M2/M3/M4/M5/M6/M10/M12/M15/M20/M30/H1/H2/
        H3/H4/H6/H8/H12/D1/W1/MN1. ``count``: positive = forward from
        ``from_``, negative = backward ending at ``from_``, omitted with no
        ``from_``/``to`` = last 100 bars. ``from_``/``to``: unix seconds or
        ``YYYY_MM_DD[_HH_MM_SS]``; ``to`` requires ``from_`` and is mutually
        exclusive with ``count``.
        """
        query = _rates_query(timeframe, count, from_, to)
        return await _call("GET", f"/symbols/{symbol}/rates", query=query)

    @mcp.tool()
    async def get_ticks(
        symbol: str,
        count: int = 0,
        from_: str = "",
        to: str = "",
        flags: str = "",
    ) -> dict[str, Any]:
        """Get raw ticks for one symbol (``GET /symbols/{symbol}/ticks``).

        ``count``: positive = forward from ``from_``, negative = backward
        ending at ``from_``, omitted with no ``from_``/``to`` = last 100
        ticks. ``from_``/``to``: unix seconds or
        ``YYYY_MM_DD[_HH_MM_SS]``; ``to`` requires ``from_`` and is mutually
        exclusive with ``count``. ``flags``: ALL / INFO / TRADE (default ALL).
        """
        query: dict[str, Any] = {}
        if count:
            query["count"] = count
        if from_:
            query["from"] = from_
        if to:
            query["to"] = to
        if flags:
            query["flags"] = flags
        return await _call("GET", f"/symbols/{symbol}/ticks", query=query or None)

    @mcp.tool()
    async def get_rates_ta(
        symbol: str,
        timeframe: str,
        indicators: dict[str, Any],
        count: int = 0,
        from_: str = "",
        to: str = "",
    ) -> dict[str, Any]:
        """Get OHLCV bars for one symbol/timeframe plus a technical-analysis
        overlay computed by the wickworks sidecar (``POST
        /symbols/{symbol}/rates/ta``).

        ``timeframe``: same values as ``get_rates``. ``indicators``: a
        non-empty wickworks indicator spec object. ``count``/``from_``/``to``:
        same semantics as ``get_rates``.
        """
        query = _rates_query(timeframe, count, from_, to)
        body = {"indicators": indicators}
        return await _call("POST", f"/symbols/{symbol}/rates/ta", query=query, body=body)

    # ── Positions ────────────────────────────────────────────────────

    @mcp.tool()
    async def list_positions() -> dict[str, Any]:
        """List all open positions (``GET /positions``)."""
        return await _call("GET", "/positions")

    @mcp.tool()
    async def get_position(ticket: int) -> dict[str, Any]:
        """Get one open position by ticket (``GET /positions/{ticket}``)."""
        return await _call("GET", f"/positions/{ticket}")

    @mcp.tool()
    async def modify_position(ticket: int, sl: float = 0, tp: float = 0) -> dict[str, Any]:
        """Modify stop-loss/take-profit on an open position (``PUT
        /positions/{ticket}``). Omitted ``sl``/``tp`` keep the position's
        current value.

        DESTRUCTIVE: changes a live position's risk parameters. Only call
        on explicit user request.
        """
        body = {"sl": sl, "tp": tp}
        return await _call("PUT", f"/positions/{ticket}", body=body)

    @mcp.tool()
    async def close_position(ticket: int, volume: float = 0, deviation: int = 0) -> dict[str, Any]:
        """Close an open position, fully or partially (``DELETE
        /positions/{ticket}``). ``volume`` omitted/0 closes the full
        position; ``deviation`` is the max allowed price slippage in points
        (default 20).

        DESTRUCTIVE: irreversible on a live account. Only call on explicit
        user request.
        """
        body: dict[str, Any] = {}
        if volume:
            body["volume"] = volume
        if deviation:
            body["deviation"] = deviation
        return await _call("DELETE", f"/positions/{ticket}", body=body or None)

    # ── Orders ───────────────────────────────────────────────────────

    @mcp.tool()
    async def list_orders() -> dict[str, Any]:
        """List all pending orders (``GET /orders``)."""
        return await _call("GET", "/orders")

    @mcp.tool()
    async def get_order(ticket: int) -> dict[str, Any]:
        """Get one pending order by ticket (``GET /orders/{ticket}``)."""
        return await _call("GET", f"/orders/{ticket}")

    @mcp.tool()
    async def create_order(
        symbol: str,
        type: str,
        volume: float,
        price: float = 0,
        sl: float = 0,
        tp: float = 0,
        deviation: int = 0,
        comment: str = "",
        magic: int = 0,
    ) -> dict[str, Any]:
        """Place a market or pending order (``POST /orders``).

        ``type``: BUY / SELL (market) or BUY_LIMIT / SELL_LIMIT / BUY_STOP /
        SELL_STOP / BUY_STOP_LIMIT / SELL_STOP_LIMIT (pending). ``price`` is
        required for pending orders; market orders fetch the current tick if
        omitted. ``sl``/``tp`` are optional stop-loss/take-profit prices.
        ``deviation`` is max allowed slippage in points (market orders).

        DESTRUCTIVE: places a real market or pending order on a live
        trading account — irreversible once filled. Only call on explicit
        user request, and confirm symbol/type/volume/price first.
        """
        body: dict[str, Any] = {"symbol": symbol, "type": type, "volume": volume}
        if price:
            body["price"] = price
        if sl:
            body["sl"] = sl
        if tp:
            body["tp"] = tp
        if deviation:
            body["deviation"] = deviation
        if comment:
            body["comment"] = comment
        if magic:
            body["magic"] = magic
        return await _call("POST", "/orders", body=body)

    @mcp.tool()
    async def modify_order(
        ticket: int,
        price: float = 0,
        sl: float = 0,
        tp: float = 0,
    ) -> dict[str, Any]:
        """Modify a pending order's price/stop-loss/take-profit (``PUT
        /orders/{ticket}``). Omitted fields keep the order's current value.

        DESTRUCTIVE: changes a live pending order. Only call on explicit
        user request.
        """
        body: dict[str, Any] = {}
        if price:
            body["price"] = price
        if sl:
            body["sl"] = sl
        if tp:
            body["tp"] = tp
        return await _call("PUT", f"/orders/{ticket}", body=body or None)

    @mcp.tool()
    async def cancel_order(ticket: int) -> dict[str, Any]:
        """Cancel a pending order (``DELETE /orders/{ticket}``).

        DESTRUCTIVE: irreversible on a live account. Only call on explicit
        user request.
        """
        return await _call("DELETE", f"/orders/{ticket}")

    # ── History ──────────────────────────────────────────────────────

    @mcp.tool()
    async def get_history_orders(from_: str = "", to: str = "") -> dict[str, Any]:
        """Get closed/cancelled orders in a date range (``GET
        /history/orders``). ``from_``/``to`` are required unix timestamps."""
        query = {"from": from_, "to": to}
        return await _call("GET", "/history/orders", query=query)

    @mcp.tool()
    async def get_history_deals(from_: str = "", to: str = "") -> dict[str, Any]:
        """Get executed deals in a date range (``GET /history/deals``).
        ``from_``/``to`` are required unix timestamps."""
        query = {"from": from_, "to": to}
        return await _call("GET", "/history/deals", query=query)

    # ── Backtest ─────────────────────────────────────────────────────

    @mcp.tool()
    async def get_backtest(job_id: str, part: str = "status") -> dict[str, Any]:
        """Poll a Strategy Tester backtest job's status or fetch its artifacts.
        ``part``: "status" (``GET /backtest/{job_id}``), "report"
        (``.../report``), "log" (``.../log``), or "tail" (``.../tail`` — live
        log tail, works while running).

        Submitting a NEW backtest is not exposed as a tool: ``POST /backtest``
        takes a multipart/form-data upload (INI + expert/.set files), which
        doesn't map to a JSON tool — submit it via the REST API directly, then
        poll the returned job here.
        """
        suffix = "" if part == "status" else f"/{part}"
        return await _call("GET", f"/backtest/{job_id}{suffix}")

    # ── Escape hatches ───────────────────────────────────────────────

    @mcp.tool()
    async def endpoints() -> dict[str, Any]:
        """List every REST endpoint (method, path) from the Flask URL map —
        the catalog of routes ``request`` can call. Discover routes here
        rather than guessing paths."""
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
        """Escape hatch: call a JSON-compatible mt5-httpapi REST endpoint and
        return its JSON response, for routes without a dedicated tool (e.g.
        ``/backtest/build-ini`` or ``/backtest/build-set``). Multipart uploads,
        including ``POST /backtest``, must use the REST API directly.

        ``method``: GET / POST / PUT / DELETE / etc. ``path``: a full route
        from ``endpoints``, e.g. ``/account`` or ``/orders``. ``query``: URL
        query params. ``body``: JSON body for POST / PUT. The call runs the
        exact same handler, auth and MT5 locking as a real HTTP request
        (in-process).

        DESTRUCTIVE for trade/order/position routes: those mutations are
        irreversible and hit a LIVE account with no client-side retry —
        only call them when the user asked for that exact action, and
        confirm the parameters first.
        """
        verb = method.upper().strip()
        target = path if path.startswith("/") else "/" + path
        return await _call(verb, target, query=query, body=body)

    return mcp


def _rates_query(timeframe: str, count: int, from_: str, to: str) -> dict[str, Any]:
    """Build the shared query dict for ``get_rates``/``get_rates_ta``."""
    query: dict[str, Any] = {"timeframe": timeframe}
    if count:
        query["count"] = count
    if from_:
        query["from"] = from_
    if to:
        query["to"] = to
    return query


async def _call(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one REST call through the in-process WSGI helper off the
    event loop, logging the route only (never body/query, which can carry
    order params)."""
    logger.info("mcp proxy request", extra={"http_method": method, "path": path})
    return await asyncio.to_thread(_call_wsgi, method, path, query, body)


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
