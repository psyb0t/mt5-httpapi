"""The unified MCP server — every mt5-httpapi tool, across every terminal.

Each tool mirrors the per-terminal MCP server's tool of the same name and adds
``broker`` / ``account`` (plus optional ``instance``) so one MCP session can
drive every configured terminal. The per-terminal ``/<broker>/<account>/mcp``
endpoints are untouched and keep working; this is an addition, not a
replacement.

Terminals are separate MT5 connections with separate balances. Naming the wrong
one places a real order on the wrong account, so no tool defaults the terminal
and every response echoes which terminal answered.
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcpunifier.client import TerminalClient
from mcpunifier.config import Settings, resolve
from mcpunifier.constants import (
    DEFAULT_INSTANCE,
    MCP_STREAMABLE_HTTP_PATH,
    SKIP_HTTP_METHODS,
)

logger = logging.getLogger(__name__)

# The REST surface of one mt5api process, mirrored from its route table. The
# unifier is out-of-process so it cannot read Flask's url_map the way the
# per-terminal server does; this list is what `endpoints` reports.
_ROUTE_CATALOG: tuple[tuple[str, str], ...] = (
    ("GET", "/ping"),
    ("GET", "/error"),
    ("GET", "/terminal"),
    ("POST", "/terminal/init"),
    ("POST", "/terminal/shutdown"),
    ("POST", "/terminal/restart"),
    ("GET", "/account"),
    ("GET", "/symbols"),
    ("GET", "/symbols/<symbol>"),
    ("GET", "/symbols/<symbol>/tick"),
    ("GET", "/symbols/<symbol>/rates"),
    ("POST", "/symbols/<symbol>/rates/ta"),
    ("GET", "/symbols/<symbol>/ticks"),
    ("GET", "/positions"),
    ("GET", "/positions/<ticket>"),
    ("PUT", "/positions/<ticket>"),
    ("DELETE", "/positions/<ticket>"),
    ("GET", "/orders"),
    ("POST", "/orders"),
    ("GET", "/orders/<ticket>"),
    ("PUT", "/orders/<ticket>"),
    ("DELETE", "/orders/<ticket>"),
    ("GET", "/history/orders"),
    ("GET", "/history/deals"),
    ("POST", "/backtest/build-ini"),
    ("POST", "/backtest/build-set"),
    ("POST", "/backtest"),
    ("GET", "/backtest/<job_id>"),
    ("GET", "/backtest/<job_id>/report"),
    ("GET", "/backtest/<job_id>/log"),
    ("GET", "/backtest/<job_id>/tail"),
)

_INSTRUCTIONS = """\
HTTP interface to EVERY configured MetaTrader 5 terminal, exposed over MCP as
dedicated typed tools. Each tool takes `broker` and `account` (plus optional
`instance`) naming which terminal to act on; call `list_terminals` first to see
what is configured, including whether each process is in live or backtest mode.

Tool families: health/terminal (ping, get_terminal, terminal_control), account
(get_account), market data (list_symbols, get_symbol, get_tick, get_rates,
get_ticks, get_rates_ta), positions (list_positions, get_position,
modify_position, close_position), orders (list_orders, get_order, create_order,
modify_order, cancel_order), history (get_history_orders, get_history_deals),
backtests (get_backtest — polling; new runs are multipart, submit via REST),
and the escape hatches `request` and `endpoints`.

Terminals are separate accounts with separate balances. Placing, modifying or
cancelling orders and modifying or closing positions are real, irreversible
actions with no client-side retry — call those only when the user asked for
that specific action, and confirm both the parameters AND which terminal
before acting.
"""


def build_mcp_server(settings: Settings, client: TerminalClient) -> FastMCP:
    """Construct the FastMCP server mounted under ``/mcp``."""
    mcp = FastMCP(
        name="mt5-httpapi-unifier",
        instructions=_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        # Headless service behind the operator's own proxy at an arbitrary
        # Host; the SDK's DNS-rebinding allowlist is a browser-localhost
        # mitigation that would 421 real-hostname deployments.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    mcp.settings.streamable_http_path = MCP_STREAMABLE_HTTP_PATH

    async def call(
        broker: str,
        account: str,
        instance: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve the terminal, dispatch, and stamp the answer with its key."""
        terminal = resolve(settings, broker, account, instance)
        result = await client.call(terminal, method, path, query=query, body=body)
        return {"terminal": terminal.key, **result}

    @mcp.tool()
    async def list_terminals() -> dict[str, Any]:
        """List every configured terminal: broker, account, instance and process
        mode (live or backtest). Call this before any other tool to learn which
        broker/account values are valid — the other tools reject anything not
        listed here rather than guessing."""
        return {
            "terminals": [
                {
                    "broker": terminal.broker,
                    "account": terminal.account,
                    "instance": terminal.instance,
                    "key": terminal.key,
                    "mode": terminal.mode,
                }
                for _, terminal in sorted(settings.terminals.items())
            ]
        }

    @mcp.tool()
    async def ping(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Lock-free liveness check for one terminal (``GET /ping``). Use this to
        tell a configured-but-down terminal from a reachable one."""
        return await call(broker, account, instance, "GET", "/ping")

    @mcp.tool()
    async def get_terminal(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get MT5 terminal info: build, broker/connection state, trade
        permissions (``GET /terminal``)."""
        return await call(broker, account, instance, "GET", "/terminal")

    @mcp.tool()
    async def terminal_control(
        broker: str,
        account: str,
        action: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Control one terminal's MT5 connection: ``action`` is "init",
        "shutdown" or "restart" (``POST /terminal/{action}``).

        ``shutdown`` disconnects that API process from the MT5 SDK while
        leaving terminal64.exe running. ``restart`` kills and relaunches that
        selected terminal process. Only call either on explicit user request,
        and confirm which terminal first.
        """
        return await call(broker, account, instance, "POST", f"/terminal/{action}")

    @mcp.tool()
    async def get_account(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one terminal's balance, equity, margin and trading permissions
        (``GET /account``)."""
        return await call(broker, account, instance, "GET", "/account")

    @mcp.tool()
    async def list_symbols(
        broker: str,
        account: str,
        group: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List tradable symbol names for one terminal, optionally filtered by a
        glob-style ``group`` pattern, e.g. ``"*USD*"`` (``GET /symbols``).
        Symbol names and suffixes differ per broker."""
        query = {"group": group} if group else None
        return await call(broker, account, instance, "GET", "/symbols", query=query)

    @mcp.tool()
    async def get_symbol(
        broker: str,
        account: str,
        symbol: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get full specification for one symbol: digits, point, contract size,
        margin/volume limits (``GET /symbols/{symbol}``)."""
        return await call(broker, account, instance, "GET", f"/symbols/{symbol}")

    @mcp.tool()
    async def get_tick(
        broker: str,
        account: str,
        symbol: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get the latest bid/ask/last tick for one symbol
        (``GET /symbols/{symbol}/tick``)."""
        return await call(broker, account, instance, "GET", f"/symbols/{symbol}/tick")

    @mcp.tool()
    async def get_rates(
        broker: str,
        account: str,
        symbol: str,
        timeframe: str,
        count: int = 0,
        from_: str = "",
        to: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get OHLCV bars for one symbol/timeframe (``GET
        /symbols/{symbol}/rates``).

        ``timeframe``: one of M1/M2/M3/M4/M5/M6/M10/M12/M15/M20/M30/H1/H2/H3/
        H4/H6/H8/H12/D1/W1/MN1. ``count``: positive = forward from ``from_``,
        negative = backward ending at ``from_``, omitted with no ``from_``/``to``
        = last 100 bars. ``from_``/``to``: unix seconds or
        ``YYYY_MM_DD[_HH_MM_SS]``; ``to`` requires ``from_`` and is mutually
        exclusive with ``count``.
        """
        query = _rates_query(timeframe, count, from_, to)
        return await call(
            broker,
            account,
            instance,
            "GET",
            f"/symbols/{symbol}/rates",
            query=query,
        )

    @mcp.tool()
    async def get_ticks(
        broker: str,
        account: str,
        symbol: str,
        count: int = 0,
        from_: str = "",
        to: str = "",
        flags: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get raw ticks for one symbol (``GET /symbols/{symbol}/ticks``).

        ``count``: positive = forward from ``from_``, negative = backward ending
        at ``from_``, omitted with no ``from_``/``to`` = last 100 ticks.
        ``from_``/``to``: unix seconds or ``YYYY_MM_DD[_HH_MM_SS]``; ``to``
        requires ``from_`` and is mutually exclusive with ``count``. ``flags``:
        ALL / INFO / TRADE (default ALL).
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
        return await call(
            broker,
            account,
            instance,
            "GET",
            f"/symbols/{symbol}/ticks",
            query=query or None,
        )

    @mcp.tool()
    async def get_rates_ta(
        broker: str,
        account: str,
        symbol: str,
        timeframe: str,
        indicators: dict[str, Any],
        count: int = 0,
        from_: str = "",
        to: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get OHLCV bars plus a technical-analysis overlay computed by the
        wickworks sidecar (``POST /symbols/{symbol}/rates/ta``).

        ``timeframe``: same values as ``get_rates``. ``indicators``: a non-empty
        wickworks indicator spec object. ``count``/``from_``/``to``: same
        semantics as ``get_rates``.
        """
        query = _rates_query(timeframe, count, from_, to)
        body = {"indicators": indicators}
        return await call(
            broker,
            account,
            instance,
            "POST",
            f"/symbols/{symbol}/rates/ta",
            query=query,
            body=body,
        )

    @mcp.tool()
    async def list_positions(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List open positions on one terminal (``GET /positions``). Tickets are
        per-terminal — a ticket from one account means nothing on another."""
        return await call(broker, account, instance, "GET", "/positions")

    @mcp.tool()
    async def get_position(
        broker: str,
        account: str,
        ticket: int,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one open position by ticket (``GET /positions/{ticket}``)."""
        return await call(broker, account, instance, "GET", f"/positions/{ticket}")

    @mcp.tool()
    async def modify_position(
        broker: str,
        account: str,
        ticket: int,
        sl: float = 0,
        tp: float = 0,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Modify stop-loss/take-profit on an open position (``PUT
        /positions/{ticket}``). Omitted ``sl``/``tp`` keep the current value.

        DESTRUCTIVE: changes a live position's risk parameters. Only call on
        explicit user request, and confirm which terminal first.
        """
        body = {"sl": sl, "tp": tp}
        return await call(
            broker,
            account,
            instance,
            "PUT",
            f"/positions/{ticket}",
            body=body,
        )

    @mcp.tool()
    async def close_position(
        broker: str,
        account: str,
        ticket: int,
        volume: float = 0,
        deviation: int = 0,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Close an open position, fully or partially (``DELETE
        /positions/{ticket}``). ``volume`` omitted/0 closes the full position;
        ``deviation`` is max allowed slippage in points (default 20).

        DESTRUCTIVE: irreversible on a live account. Only call on explicit user
        request, and confirm both the ticket AND which terminal first.
        """
        body: dict[str, Any] = {}
        if volume:
            body["volume"] = volume
        if deviation:
            body["deviation"] = deviation
        return await call(
            broker,
            account,
            instance,
            "DELETE",
            f"/positions/{ticket}",
            body=body or None,
        )

    @mcp.tool()
    async def list_orders(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List pending orders on one terminal (``GET /orders``)."""
        return await call(broker, account, instance, "GET", "/orders")

    @mcp.tool()
    async def get_order(
        broker: str,
        account: str,
        ticket: int,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one pending order by ticket (``GET /orders/{ticket}``)."""
        return await call(broker, account, instance, "GET", f"/orders/{ticket}")

    @mcp.tool()
    async def create_order(
        broker: str,
        account: str,
        symbol: str,
        type: str,
        volume: float,
        price: float = 0,
        sl: float = 0,
        tp: float = 0,
        deviation: int = 0,
        comment: str = "",
        magic: int = 0,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Place a market or pending order on one terminal (``POST /orders``).

        ``type``: BUY / SELL (market) or BUY_LIMIT / SELL_LIMIT / BUY_STOP /
        SELL_STOP / BUY_STOP_LIMIT / SELL_STOP_LIMIT (pending). ``price`` is
        required for pending orders; market orders fetch the current tick if
        omitted. ``sl``/``tp`` are optional stop-loss/take-profit prices.
        ``deviation`` is max allowed slippage in points (market orders).

        DESTRUCTIVE: places a real order on a real account — irreversible once
        filled. Only call on explicit user request, and confirm
        terminal/symbol/type/volume/price first. Call ``get_account`` for the
        selected terminal and inspect its trade mode before trading;
        ``list_terminals`` reports process mode, not live/demo account status.
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
        return await call(broker, account, instance, "POST", "/orders", body=body)

    @mcp.tool()
    async def modify_order(
        broker: str,
        account: str,
        ticket: int,
        price: float = 0,
        sl: float = 0,
        tp: float = 0,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Modify a pending order's price/stop-loss/take-profit (``PUT
        /orders/{ticket}``). Omitted fields keep the current value.

        DESTRUCTIVE: changes a live pending order. Only call on explicit user
        request, and confirm which terminal first.
        """
        body: dict[str, Any] = {}
        if price:
            body["price"] = price
        if sl:
            body["sl"] = sl
        if tp:
            body["tp"] = tp
        return await call(
            broker,
            account,
            instance,
            "PUT",
            f"/orders/{ticket}",
            body=body or None,
        )

    @mcp.tool()
    async def cancel_order(
        broker: str,
        account: str,
        ticket: int,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Cancel a pending order (``DELETE /orders/{ticket}``).

        DESTRUCTIVE: irreversible on a live account. Only call on explicit user
        request, and confirm which terminal first.
        """
        return await call(broker, account, instance, "DELETE", f"/orders/{ticket}")

    @mcp.tool()
    async def get_history_orders(
        broker: str,
        account: str,
        from_: str = "",
        to: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get closed/cancelled orders in a date range (``GET /history/orders``).
        ``from_``/``to`` are required unix timestamps."""
        query = {"from": from_, "to": to}
        return await call(
            broker,
            account,
            instance,
            "GET",
            "/history/orders",
            query=query,
        )

    @mcp.tool()
    async def get_history_deals(
        broker: str,
        account: str,
        from_: str = "",
        to: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get executed deals in a date range (``GET /history/deals``).
        ``from_``/``to`` are required unix timestamps."""
        query = {"from": from_, "to": to}
        return await call(
            broker,
            account,
            instance,
            "GET",
            "/history/deals",
            query=query,
        )

    @mcp.tool()
    async def get_backtest(
        broker: str,
        account: str,
        job_id: str,
        part: str = "status",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Poll a Strategy Tester backtest job's status or fetch its artifacts.
        ``part``: "status" (``GET /backtest/{job_id}``), "report"
        (``.../report``), "log" (``.../log``), or "tail" (``.../tail`` — live log
        tail, works while running).

        Submitting a NEW backtest is not exposed as a tool: ``POST /backtest``
        takes a multipart/form-data upload, which doesn't map to a JSON tool —
        submit it via that terminal's REST API, then poll the job here.
        """
        suffix = "" if part == "status" else f"/{part}"
        return await call(
            broker,
            account,
            instance,
            "GET",
            f"/backtest/{job_id}{suffix}",
        )

    @mcp.tool()
    async def endpoints() -> dict[str, Any]:
        """List every REST endpoint (method, path) one terminal exposes — the
        catalog of routes ``request`` can call. Paths are the same on every
        terminal; ``request`` picks which terminal via broker/account."""
        found = [
            {"method": method, "path": path}
            for method, path in _ROUTE_CATALOG
            if method not in SKIP_HTTP_METHODS
        ]
        found.sort(key=lambda entry: (entry["path"], entry["method"]))
        return {"endpoints": found}

    @mcp.tool()
    async def request(
        broker: str,
        account: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Escape hatch: call a JSON-compatible mt5-httpapi REST endpoint on
        one terminal and return its JSON response, for routes without a
        dedicated tool. Multipart uploads, including ``POST /backtest``, must
        use the REST API directly.

        ``method``: GET / POST / PUT / DELETE. ``path``: a route from
        ``endpoints``, e.g. ``/account``. ``query``: URL query params. ``body``:
        JSON body for POST / PUT.

        DESTRUCTIVE for trade/order/position routes: those mutations are
        irreversible and hit a real account with no client-side retry — only
        call them when the user asked for that exact action, and confirm the
        parameters and the terminal first.
        """
        verb = method.upper().strip()
        target = path if path.startswith("/") else f"/{path}"
        return await call(
            broker,
            account,
            instance,
            verb,
            target,
            query=query,
            body=body,
        )

    logger.info(
        "unified MCP server built",
        extra={"terminals": len(settings.terminals)},
    )
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
