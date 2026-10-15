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

import base64
import binascii
import codecs
import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

from mcpunifier.client import TerminalClient
from mcpunifier.config import Settings, Terminal, resolve
from mcpunifier.constants import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_PNG,
    DEFAULT_INSTANCE,
    FEATURE_CHARTCTL,
    FEATURE_FILES,
    FILES_MAX_INLINE_BYTES,
    FILES_TIMEOUT_SECONDS,
    IMAGE_FORMAT_PNG,
    MCP_STREAMABLE_HTTP_PATH,
    SCREENSHOT_DEFAULT_HEIGHT,
    SCREENSHOT_DEFAULT_WIDTH,
    SKIP_HTTP_METHODS,
    WEBREQUEST_TIMEOUT_SECONDS,
)
from mcpunifier.errors import (
    ChartctlDisabled,
    FilesDisabled,
    TerminalRejected,
    ToolArgumentError,
    UnexpectedContent,
)

logger = logging.getLogger(__name__)

_HTTP_NOT_FOUND = 404
# Multipart form field names the chartctl upload handlers read.
_EXPERT_FORM_FIELD = "expert"
_SET_FORM_FIELD = "set"
_RUNAS_QUERY = {"runas": "1"}
# Path-segment values that would address a different route than the one named.
_RELATIVE_PATH_SEGMENTS = frozenset({"", ".", ".."})
_PATH_SEPARATORS = ("/", "\\")
# File API: the route prefix of each tree and the multipart field it reads.
_FILE_TREE_PREFIXES = {"terminal": "/files", "compile": "/compile/files"}
_FILE_FORM_FIELD = "file"
_DOT_SEGMENTS = frozenset({".", ".."})
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_FLAG_ON = "1"

# The REST surface of one mt5api process, mirrored from its route table. The
# unifier is out-of-process so it cannot read Flask's url_map the way the
# per-terminal server does; this list is what `endpoints` reports.
_ROUTE_CATALOG: tuple[tuple[str, str], ...] = (
    ("GET", "/ping"),
    ("GET", "/error"),
    ("GET", "/busy"),
    ("GET", "/terminal"),
    ("POST", "/terminal/init"),
    ("POST", "/terminal/shutdown"),
    ("POST", "/terminal/restart"),
    ("GET", "/account"),
    ("GET", "/symbols"),
    ("POST", "/symbols/import"),
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
    ("POST", "/compile"),
)

# Routes a terminal registers only when chartctl is enabled for it
# (mt5api/handlers/chartctl_routes.py); everywhere else they answer 404.
_CHARTCTL_ROUTE_CATALOG: tuple[tuple[str, str], ...] = (
    ("POST", "/experts"),
    ("GET", "/experts"),
    ("DELETE", "/experts/<name>"),
    ("POST", "/sets"),
    ("GET", "/sets"),
    ("GET", "/sets/<name>"),
    ("DELETE", "/sets/<name>"),
    ("POST", "/deployments"),
    ("GET", "/deployments"),
    ("POST", "/deployments/reconcile"),
    ("GET", "/deployments/<dep_id>"),
    ("PATCH", "/deployments/<dep_id>"),
    ("DELETE", "/deployments/<dep_id>"),
    ("GET", "/charts"),
    ("GET", "/loader"),
    ("POST", "/charts/<chart_id>/screenshot"),
    ("POST", "/charts/<chart_id>/close"),
    ("GET", "/webrequest"),
    ("PUT", "/webrequest"),
    ("POST", "/webrequest/apply"),
    ("POST", "/webrequest/verify"),
)

# Routes a terminal registers only when the file API is enabled for it
# (mt5api/handlers/files.py).
_FILES_ROUTE_CATALOG: tuple[tuple[str, str], ...] = (
    ("GET", "/files"),
    ("GET", "/files/<rel>"),
    ("PUT", "/files/<rel>"),
    ("DELETE", "/files/<rel>"),
    ("GET", "/compile/files"),
    ("GET", "/compile/files/<rel>"),
    ("PUT", "/compile/files/<rel>"),
    ("DELETE", "/compile/files/<rel>"),
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

Chart Deployments, on terminals where `list_terminals` reports chartctl true:
stage EA files (upload_expert, upload_set, list_experts, list_sets, get_set,
delete_expert, delete_set), declare deployments that a loader EA attaches to charts
(create_deployment, list_deployments, get_deployment, update_deployment,
delete_deployment, reconcile_deployments), inspect and capture charts
(list_charts, get_loader, screenshot_chart, close_chart), and manage the
WebRequest URL allowlist (get_webrequest, set_webrequest, apply_webrequest,
verify_webrequest).
Uploads take the file as base64; screenshot_chart returns a PNG image.

File API, on terminals where `list_terminals` reports files true: list_files,
get_file, put_file (with extract it unpacks a zip into a directory) and
delete_file, on the terminal's install directory (tree "terminal": MQL5/Include,
MQL5/Libraries, MQL5/Files, logs, ...) or on the MQL5 tree compile builds
against (tree "compile"). Writing into MQL5/Experts, MQL5/Libraries or
MQL5/Include changes what experts on that account run, so only on request.

Terminals are separate accounts with separate balances. Placing, modifying or
cancelling orders and modifying or closing positions are real, irreversible
actions with no client-side retry — call those only when the user asked for
that specific action, and confirm both the parameters AND which terminal
before acting. A running deployment is a live EA that can trade, so the same
applies to creating, enabling, re-pointing or deleting one.
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

    async def call_chartctl(
        broker: str,
        account: str,
        instance: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """``call`` for a chartctl route, reporting a terminal without the
        routes as ChartctlDisabled instead of a bare 404."""
        terminal = resolve(settings, broker, account, instance)
        try:
            result = await client.call(
                terminal,
                method,
                path,
                query=query,
                body=body,
                files=files,
                timeout=timeout,
            )
        except TerminalRejected as err:
            raise _chartctl_error(terminal, err) from err
        return {"terminal": terminal.key, **result}

    async def call_files(
        broker: str,
        account: str,
        instance: str,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> dict[str, Any]:
        """``call`` for a file API route, reporting a terminal without the
        routes as FilesDisabled instead of a bare 404."""
        terminal = resolve(settings, broker, account, instance)
        try:
            result = await client.call(
                terminal,
                method,
                path,
                query=query,
                files=files,
                timeout=FILES_TIMEOUT_SECONDS,
            )
        except TerminalRejected as err:
            raise _files_error(terminal, err) from err
        return {"terminal": terminal.key, **result}

    async def fetch_file(
        broker: str,
        account: str,
        instance: str,
        path: str,
    ) -> tuple[Terminal, str, bytes]:
        """GET a file API path: (terminal, content type, body)."""
        terminal = resolve(settings, broker, account, instance)
        try:
            content_type, data = await client.call_bytes(terminal, "GET", path)
        except TerminalRejected as err:
            raise _files_error(terminal, err) from err
        return terminal, content_type, data

    @mcp.tool()
    async def list_terminals() -> dict[str, Any]:
        """List every configured terminal: broker, account, instance, process
        mode (live or backtest) and whether Chart Deployments (chartctl) and
        the file API (files) are enabled for it in config.yaml. Call this
        before any other tool to learn which broker/account values are
        valid; the other tools reject anything not listed here rather than
        guessing."""
        return {
            "terminals": [
                {
                    "broker": terminal.broker,
                    "account": terminal.account,
                    "instance": terminal.instance,
                    "key": terminal.key,
                    "mode": terminal.mode,
                    "chartctl": terminal.chartctl,
                    "files": terminal.files,
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

    # ── Chart Deployments: artifacts ─────────────────────────────────

    @mcp.tool()
    async def list_experts(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List one terminal's staged expert ``.ex5`` files with size and
        sha256, both uploaded and host-managed (``GET /experts``)."""
        return await call_chartctl(broker, account, instance, "GET", "/experts")

    @mcp.tool()
    async def upload_expert(
        broker: str,
        account: str,
        filename: str,
        content_base64: str,
        overwrite: bool = False,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Stage a compiled expert on one terminal (``POST /experts``).

        ``filename``: the ``.ex5`` file name, e.g. ``"MyEA.ex5"``.
        ``content_base64``: the file's bytes, base64-encoded. Re-uploading
        identical bytes is a no-op (``skipped``); different bytes under an
        existing name are refused with 409 unless ``overwrite`` is true.
        Files over ``chartctl.max_upload_bytes`` (16 MiB by default) are
        refused with 400. The ``/mcp`` request itself is capped at 25 MiB,
        about 18 MiB of file after base64.

        ``overwrite`` replaces the file a running deployment of that expert
        loads, so treat it like a deployment change: only on explicit user
        request.

        MT5 loads only experts it has seen, so the upload also refreshes the
        terminal's Navigator. ``navigator_refresh`` in the response: ``ok``
        (deployable now), ``failed`` (upload the same file again to retry),
        or ``unavailable`` (no GUI automation on that host: the terminal
        must restart before a deployment of this expert can attach).
        """
        content = _decode_base64("content_base64", content_base64)
        query = {"overwrite": "true"} if overwrite else None
        files = {_EXPERT_FORM_FIELD: (filename, content)}
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/experts",
            query=query,
            files=files,
        )

    @mcp.tool()
    async def delete_expert(
        broker: str,
        account: str,
        name: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Remove a staged expert ``.ex5`` from one terminal
        (``DELETE /experts/{name}``). Refused with 409 while a deployment
        uses it, and with 403 for a host-managed file that was never
        deployed."""
        segment = _path_segment("name", name)
        return await call_chartctl(
            broker,
            account,
            instance,
            "DELETE",
            f"/experts/{segment}",
        )

    @mcp.tool()
    async def list_sets(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List one terminal's staged ``.set`` parameter files, uploaded and
        host-managed (``GET /sets``)."""
        return await call_chartctl(broker, account, instance, "GET", "/sets")

    @mcp.tool()
    async def get_set(
        broker: str,
        account: str,
        name: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one staged ``.set`` file parsed into its inputs
        (``GET /sets/{name}``)."""
        segment = _path_segment("name", name)
        return await call_chartctl(
            broker,
            account,
            instance,
            "GET",
            f"/sets/{segment}",
        )

    @mcp.tool()
    async def delete_set(
        broker: str,
        account: str,
        name: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Remove a staged ``.set`` file from one terminal
        (``DELETE /sets/{name}``). Refused with 409 while a deployment uses
        it, and with 403 for a host-managed file."""
        segment = _path_segment("name", name)
        return await call_chartctl(
            broker,
            account,
            instance,
            "DELETE",
            f"/sets/{segment}",
        )

    @mcp.tool()
    async def upload_set(
        broker: str,
        account: str,
        filename: str,
        content: str = "",
        content_base64: str = "",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Stage a ``.set`` parameter file on one terminal (``POST /sets``)
        and get its parsed inputs back. Replaces a staged file of the same
        name.

        Pass exactly one of ``content`` (the file as text, ``Name=value``
        per line) or ``content_base64`` (the raw bytes, for a UTF-16 file
        exported by MT5). Check the returned ``inputs``: a file with no
        ``Name=value`` lines parses to none, and an expert deployed with it
        runs on its default inputs.
        """
        data = _set_file_bytes(content, content_base64)
        files = {_SET_FORM_FIELD: (filename, data)}
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/sets",
            files=files,
        )

    # ── Chart Deployments: deployments ───────────────────────────────

    @mcp.tool()
    async def list_deployments(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List one terminal's deployments merged with what the loader EA
        observes: status, chart id, errors, and whether the terminal has
        converged on the desired state (``GET /deployments``)."""
        return await call_chartctl(broker, account, instance, "GET", "/deployments")

    @mcp.tool()
    async def get_deployment(
        broker: str,
        account: str,
        deployment_id: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one deployment and its observed status
        (``GET /deployments/{deployment_id}``)."""
        segment = _path_segment("deployment_id", deployment_id)
        return await call_chartctl(
            broker,
            account,
            instance,
            "GET",
            f"/deployments/{segment}",
        )

    @mcp.tool()
    async def create_deployment(
        broker: str,
        account: str,
        expert: str,
        symbol: str,
        timeframe: str,
        set_file: str = "",
        enabled: bool = True,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Declare a deployment: run a staged expert on a chart of one
        terminal (``POST /deployments``). The loader EA opens the chart and
        attaches the expert on its next pass. Poll ``get_deployment`` until
        the status is ``running``; stop polling on ``failed`` or
        ``degraded`` and report its ``error``, and check ``get_loader`` if it
        stays ``pending`` (nothing attaches while the loader is not alive).

        ``expert``: a staged ``.ex5`` name. ``symbol``: the broker's symbol
        name. ``timeframe``: one of M1/M2/M3/M4/M5/M6/M10/M12/M15/M20/M30/
        H1/H2/H3/H4/H6/H8/H12/D1/W1/MN1. ``set_file``: optional staged
        ``.set`` name for the expert's inputs. ``enabled``: false creates it
        paused. Creating an enabled deployment for a symbol/timeframe pair
        that another enabled deployment already targets is refused with 409
        ``DUPLICATE_CHART``.

        DESTRUCTIVE: an enabled deployment is a live EA that can trade on
        that account. Only call on explicit user request, and confirm which
        terminal first.
        """
        body: dict[str, Any] = {
            "expert": expert,
            "symbol": symbol,
            "timeframe": timeframe,
            "enabled": enabled,
        }
        if set_file:
            body["set"] = set_file
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/deployments",
            body=body,
        )

    @mcp.tool()
    async def update_deployment(
        broker: str,
        account: str,
        deployment_id: str,
        enabled: bool | None = None,
        set_file: str | None = None,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Pause, resume or re-point a deployment
        (``PATCH /deployments/{deployment_id}``).

        ``enabled``: false pauses it (the loader closes its chart), true
        resumes it; resuming is refused with 409 ``DUPLICATE_CHART`` while
        another enabled deployment targets the same symbol/timeframe.
        ``set_file``: a staged ``.set`` name to switch to, or ``""`` to run
        on the expert's default inputs. Pass at least one.

        A new ``set_file`` does NOT reach an expert that is already running:
        the loader leaves a chart it owns alone, so the new inputs apply the
        next time it opens the chart. To apply them now, pause the
        deployment, wait until ``list_charts`` no longer shows its chart,
        then resume it. ``get_deployment`` reports ``paused`` as soon as the
        pause is stored, before the loader has closed anything, so do not
        wait on that. Nothing moves while ``get_loader`` reports the loader
        not alive.

        DESTRUCTIVE: changes what a live EA does on that account. Only call
        on explicit user request, and confirm which terminal first.
        """
        body = _deployment_changes(enabled, set_file)
        segment = _path_segment("deployment_id", deployment_id)
        return await call_chartctl(
            broker,
            account,
            instance,
            "PATCH",
            f"/deployments/{segment}",
            body=body,
        )

    @mcp.tool()
    async def delete_deployment(
        broker: str,
        account: str,
        deployment_id: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Delete a deployment; the loader EA closes its chart
        (``DELETE /deployments/{deployment_id}``).

        DESTRUCTIVE: stops a live EA on that account. Only call on explicit
        user request, and confirm which terminal first.
        """
        segment = _path_segment("deployment_id", deployment_id)
        return await call_chartctl(
            broker,
            account,
            instance,
            "DELETE",
            f"/deployments/{segment}",
        )

    @mcp.tool()
    async def reconcile_deployments(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Bump one terminal's desired-state revision
        (``POST /deployments/reconcile``). The loader already reconciles on
        every pass, so this changes nothing by itself; compare the returned
        ``revision`` with ``get_loader``'s ``applied_revision`` to see when
        the loader has caught up."""
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/deployments/reconcile",
        )

    # ── Chart Deployments: charts and loader ─────────────────────────

    @mcp.tool()
    async def list_charts(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List the charts open in one terminal with their symbol, timeframe,
        attached expert and owning deployment, as the loader EA last reported
        them (``GET /charts``)."""
        return await call_chartctl(broker, account, instance, "GET", "/charts")

    @mcp.tool()
    async def get_loader(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get one terminal's loader EA status: alive, version, and which
        desired revision it has applied (``GET /loader``). If ``alive`` is
        false, no deployment will change until the loader runs."""
        return await call_chartctl(broker, account, instance, "GET", "/loader")

    @mcp.tool()
    async def screenshot_chart(
        broker: str,
        account: str,
        chart_id: int,
        width: int = SCREENSHOT_DEFAULT_WIDTH,
        height: int = SCREENSHOT_DEFAULT_HEIGHT,
        instance: str = DEFAULT_INSTANCE,
    ) -> Image:
        """Capture one chart of one terminal as a PNG image
        (``POST /charts/{chart_id}/screenshot``). Take ``chart_id`` from
        ``list_charts`` or ``get_deployment``."""
        terminal = resolve(settings, broker, account, instance)
        try:
            content_type, data = await client.call_bytes(
                terminal,
                "POST",
                f"/charts/{chart_id}/screenshot",
                query={"width": width, "height": height},
            )
        except TerminalRejected as err:
            raise _chartctl_error(terminal, err) from err
        if not content_type.startswith(CONTENT_TYPE_PNG):
            raise UnexpectedContent(terminal.key, CONTENT_TYPE_PNG, content_type)
        return Image(data=data, format=IMAGE_FORMAT_PNG)

    @mcp.tool()
    async def close_chart(
        broker: str,
        account: str,
        chart_id: int,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Close one chart of one terminal by id, including charts no
        deployment owns (``POST /charts/{chart_id}/close``). The loader
        refuses to close its own chart. A chart that belongs to an enabled
        deployment is opened again on the loader's next pass; to stop that
        expert, pause or delete the deployment instead.

        DESTRUCTIVE: any expert on that chart stops. Only call on explicit
        user request, and confirm which terminal first.
        """
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            f"/charts/{chart_id}/close",
        )

    # ── WebRequest allowlist ─────────────────────────────────────────

    @mcp.tool()
    async def get_webrequest(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Get the URLs one terminal's experts may call with
        ``WebRequest()`` (``GET /webrequest``)."""
        return await call_chartctl(broker, account, instance, "GET", "/webrequest")

    @mcp.tool()
    async def set_webrequest(
        broker: str,
        account: str,
        urls: list[str] | None = None,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        runas: bool = False,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Change one terminal's ``WebRequest()`` URL allowlist and apply it
        now (``PUT /webrequest``).

        Either replace the whole list with ``urls`` (``[]`` clears it) or
        edit it with ``add`` and/or ``remove``; not both. A patch changes only
        the URLs named and keeps every other entry the terminal holds,
        including ones added by hand in the VM, which then become part of
        the stored list. Only http(s) URLs with a host and no spaces, ``;``
        or control characters are accepted; anything else is refused with
        400 ``INVALID_URL``. ``runas``: ignored (kept for older callers).

        Inside the Windows VM this drives the terminal's Options dialog. On
        a bare-metal terminal it rewrites ``common.ini`` and RESTARTS the
        terminal. Only call on explicit user request.

        The new list is stored before it is applied, so it is kept even when
        the apply fails, and applied again whenever the API starts or
        restarts the terminal. Applying takes seconds; while a backtest runs
        on the terminal the call answers 409 and should be retried after it.
        ``get_webrequest`` shows the stored list and the last apply result;
        ``verify_webrequest`` reads the list the terminal really holds.
        """
        body = _webrequest_changes(urls, add, remove)
        query = _RUNAS_QUERY if runas else None
        return await call_chartctl(
            broker,
            account,
            instance,
            "PUT",
            "/webrequest",
            query=query,
            body=body,
            timeout=WEBREQUEST_TIMEOUT_SECONDS,
        )

    @mcp.tool()
    async def apply_webrequest(
        broker: str,
        account: str,
        runas: bool = False,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Re-apply one terminal's stored ``WebRequest()`` allowlist
        (``POST /webrequest/apply``). Inside the Windows VM the terminal
        forgets the list whenever it restarts. The API re-applies it when
        its process starts and after every terminal restart it performs
        itself, so call this after a restart from outside the API, or when
        ``get_webrequest`` shows the list but an expert's ``WebRequest()``
        is still refused. ``runas``: as in ``set_webrequest``.

        A stored URL the terminal will not take is skipped and listed under
        ``rejected`` instead of failing the whole apply. On a bare-metal
        terminal this RESTARTS the terminal.
        """
        query = _RUNAS_QUERY if runas else None
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/webrequest/apply",
            query=query,
            timeout=WEBREQUEST_TIMEOUT_SECONDS,
        )

    @mcp.tool()
    async def verify_webrequest(
        broker: str,
        account: str,
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Read the ``WebRequest()`` allowlist one terminal really holds,
        beside the stored one (``POST /webrequest/verify``). Inside the
        Windows VM only.

        It opens the terminal's Options dialog for a moment, so it answers
        429 when called again within 10 seconds and 409 while a backtest
        runs. Use it to check an apply, not to poll.
        """
        return await call_chartctl(
            broker,
            account,
            instance,
            "POST",
            "/webrequest/verify",
            timeout=WEBREQUEST_TIMEOUT_SECONDS,
        )

    # ── File API ─────────────────────────────────────────────────────

    @mcp.tool()
    async def list_files(
        broker: str,
        account: str,
        path: str = "",
        tree: str = "terminal",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """List one directory of one terminal's files (``GET /files/{path}``).

        ``tree``: ``terminal`` (default) is the terminal's install directory,
        the folder holding ``terminal64.exe``: ``MQL5/Include``,
        ``MQL5/Libraries``, ``MQL5/Files``, ``MQL5/Logs``, ``logs`` and so
        on. ``compile`` is the MQL5 directory ``POST /compile`` builds
        against, where shared ``.mqh`` libraries go. ``path``: relative to
        the tree root, ``/``-separated; empty lists the root.

        Each entry carries what ``ls -al`` shows: ``name``, ``path``,
        ``type`` (``file`` or ``dir``), ``size`` and ``size_human``,
        ``mode``, the Windows ``attributes``, ``nlink``, created, modified
        and accessed times (epoch ``*_at`` and ISO), ``is_symlink`` (with
        ``link_target``), and whether it is ``readable`` and ``writable``
        through this API.
        """
        url = _file_url(tree, path)
        terminal, content_type, data = await fetch_file(broker, account, instance, url)
        if not content_type.startswith(CONTENT_TYPE_JSON):
            raise ToolArgumentError(f"{path} is a file; use get_file to read it")
        return {"terminal": terminal.key, **json.loads(data.decode("utf-8"))}

    @mcp.tool()
    async def get_file(
        broker: str,
        account: str,
        path: str,
        tree: str = "terminal",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Read one file of one terminal (``GET /files/{path}``).

        ``tree`` and ``path`` as in ``list_files``. Returns ``size``,
        ``sha256`` and the content: ``text`` for UTF-8 or UTF-16 text (MT5
        writes its logs as UTF-16), otherwise ``content_base64``. Files over
        16 MiB are refused; download those through the REST API.
        ``mt5start.ini`` and ``Config/accounts.dat`` hold the broker
        credentials and are never returned.
        """
        url = _file_url(tree, path)
        terminal, content_type, data = await fetch_file(broker, account, instance, url)
        if content_type.startswith(CONTENT_TYPE_JSON):
            raise ToolArgumentError(f"{path} is a directory; use list_files")
        return {"terminal": terminal.key, **_file_payload(path, data)}

    @mcp.tool()
    async def put_file(
        broker: str,
        account: str,
        path: str,
        content: str = "",
        content_base64: str = "",
        extract: bool = False,
        tree: str = "terminal",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Create or replace one file on one terminal, creating its
        directories (``PUT /files/{path}``).

        Pass exactly one of ``content`` (text, written as UTF-8) or
        ``content_base64`` (raw bytes). With ``extract`` true the bytes must
        be a zip, and ``path`` names the directory it unpacks into: the
        archive's tree is merged in, replacing files of the same name and
        leaving the rest, and the zip itself is never stored. ``tree`` as in
        ``list_files``.

        Refused: paths outside the tree, ``mt5start.ini`` and
        ``Config/accounts.dat``, the terminal's executables, and Chart
        Deployments' own files (``MQL5/Experts/Uploaded``,
        ``MQL5/Files/chartctl``, ``chartctl``). The ``/mcp`` request is
        capped at 25 MiB, about 18 MiB of file after base64.

        Writing into ``MQL5/Experts``, ``MQL5/Libraries`` or
        ``MQL5/Include`` changes what experts on that account run. Only do
        it on explicit user request, and confirm which terminal first.
        """
        data = _file_bytes(content, content_base64)
        url = _file_url(tree, path, allow_root=False)
        query = {"extract": _FLAG_ON} if extract else None
        filename = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        files = {_FILE_FORM_FIELD: (filename, data)}
        return await call_files(
            broker,
            account,
            instance,
            "PUT",
            url,
            query=query,
            files=files,
        )

    @mcp.tool()
    async def delete_file(
        broker: str,
        account: str,
        path: str,
        recursive: bool = False,
        tree: str = "terminal",
        instance: str = DEFAULT_INSTANCE,
    ) -> dict[str, Any]:
        """Delete a file, or a directory, on one terminal
        (``DELETE /files/{path}``).

        A non-empty directory needs ``recursive`` true. The same paths
        ``put_file`` refuses cannot be deleted, nor a directory holding any
        of them. ``tree`` as in ``list_files``. Only on explicit user
        request, and confirm which terminal first.
        """
        url = _file_url(tree, path, allow_root=False)
        query = {"recursive": _FLAG_ON} if recursive else None
        return await call_files(broker, account, instance, "DELETE", url, query=query)

    @mcp.tool()
    async def endpoints() -> dict[str, Any]:
        """List every REST endpoint (method, path) one terminal exposes — the
        catalog of routes ``request`` can call. Paths are the same on every
        terminal; ``request`` picks which terminal via broker/account.
        Entries with ``"requires": "chartctl"`` or ``"requires": "files"``
        exist only on terminals where ``list_terminals`` reports that
        feature true; elsewhere they answer 404."""
        found = [
            {"method": method, "path": path}
            for method, path in _ROUTE_CATALOG
            if method not in SKIP_HTTP_METHODS
        ]
        found.extend(
            {"method": method, "path": path, "requires": FEATURE_CHARTCTL}
            for method, path in _CHARTCTL_ROUTE_CATALOG
            if method not in SKIP_HTTP_METHODS
        )
        found.extend(
            {"method": method, "path": path, "requires": FEATURE_FILES}
            for method, path in _FILES_ROUTE_CATALOG
            if method not in SKIP_HTTP_METHODS
        )
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
        dedicated tool. It cannot send a multipart upload: stage chart files
        with ``upload_expert`` / ``upload_set``, and submit ``POST /backtest``
        through the REST API directly.

        ``method``: GET / POST / PUT / PATCH / DELETE. ``path``: a route from
        ``endpoints``, e.g. ``/account``. ``query``: URL query params. ``body``:
        JSON body for POST / PUT / PATCH.

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


def _chartctl_error(terminal: Terminal, err: TerminalRejected) -> Exception:
    """Turn a chartctl call's rejection into the error to raise.

    A terminal without the chartctl routes answers Flask's HTML 404. The
    chartctl handlers' own 404s (unknown deployment, unstaged file) are JSON
    with a ``code``, and pass through unchanged.
    """
    if err.status != _HTTP_NOT_FOUND or _is_json_object(err.body):
        return err
    logger.info(
        "chartctl route missing on terminal",
        extra={"terminal": terminal.key, "reason": "chartctl_disabled"},
    )
    return ChartctlDisabled(terminal.key, configured=terminal.chartctl)


def _files_error(terminal: Terminal, err: TerminalRejected) -> Exception:
    """Turn a file API call's rejection into the error to raise: Flask's
    HTML 404 means the routes are missing, while the handlers' own errors
    are JSON with a ``code`` and pass through unchanged."""
    if err.status != _HTTP_NOT_FOUND or _is_json_object(err.body):
        return err
    logger.info(
        "file API route missing on terminal",
        extra={"terminal": terminal.key, "reason": "files_disabled"},
    )
    return FilesDisabled(terminal.key, configured=terminal.files)


def _file_url(tree: str, path: str, allow_root: bool = True) -> str:
    """The file API URL for ``path`` in ``tree``, each segment encoded.

    Dot segments are refused here because httpx resolves them before the
    terminal could refuse them.
    """
    prefix = _FILE_TREE_PREFIXES.get(tree)
    if prefix is None:
        raise ToolArgumentError(
            f"tree must be one of {sorted(_FILE_TREE_PREFIXES)}, got {tree!r}"
        )
    text = path.replace("\\", "/")
    if text.startswith("/"):
        raise ToolArgumentError(f"path {path!r} must be relative to the tree root")
    segments = [segment for segment in text.rstrip("/").split("/") if segment]
    if any(segment in _DOT_SEGMENTS for segment in segments):
        raise ToolArgumentError(f"path {path!r} must not contain '.' or '..' segments")
    if not segments:
        if not allow_root:
            raise ToolArgumentError("path is empty; name a file or directory")
        return prefix
    return prefix + "/" + "/".join(quote(segment, safe="") for segment in segments)


def _file_bytes(content: str, content_base64: str) -> bytes:
    """A file's bytes from exactly one of text or base64 content. Empty
    text is a valid (empty) file."""
    if content and content_base64:
        raise ToolArgumentError("pass either content or content_base64, not both")
    if content_base64:
        return _decode_base64("content_base64", content_base64)
    return content.encode("utf-8")


def _file_payload(path: str, data: bytes) -> dict[str, Any]:
    """The get_file result: metadata plus the content as text when it is
    text, base64 otherwise."""
    if len(data) > FILES_MAX_INLINE_BYTES:
        raise ToolArgumentError(
            f"{path} is {len(data)} bytes; get_file returns at most "
            f"{FILES_MAX_INLINE_BYTES}, download it through the REST API"
        )
    payload: dict[str, Any] = {
        "path": path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    text = _as_text(data)
    if text is None:
        payload["content_base64"] = base64.b64encode(data).decode("ascii")
        return payload
    payload["text"] = text
    return payload


def _as_text(data: bytes) -> str | None:
    """The bytes as text if they are UTF-16 with a BOM or clean UTF-8."""
    if data.startswith(_UTF16_BOMS):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _path_segment(field: str, value: str) -> str:
    """One URL path segment built from a tool argument, percent-encoded.

    A name such as ``../deployments/dep_x`` would otherwise reach a
    different route, since httpx resolves dot segments, and ``?`` or ``#``
    would cut the path short.
    """
    has_separator = any(sep in value for sep in _PATH_SEPARATORS)
    if value in _RELATIVE_PATH_SEGMENTS or has_separator:
        raise ToolArgumentError(
            f"{field} must be a single name without slashes, got {value!r}"
        )
    return quote(value, safe="")


def _decode_base64(field: str, value: str) -> bytes:
    """Decode a base64 tool argument, refusing empty or malformed input.
    Whitespace, such as the line breaks ``base64`` inserts, is ignored."""
    compact = "".join(value.split())
    if not compact:
        raise ToolArgumentError(f"{field} is empty")
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ToolArgumentError(f"{field} is not valid base64") from err


def _set_file_bytes(content: str, content_base64: str) -> bytes:
    """The ``.set`` bytes from exactly one of text or base64 content."""
    if bool(content) == bool(content_base64):
        raise ToolArgumentError("pass exactly one of content or content_base64")
    if content:
        return content.encode("utf-8")
    return _decode_base64("content_base64", content_base64)


def _deployment_changes(
    enabled: bool | None,
    set_file: str | None,
) -> dict[str, Any]:
    """The ``PATCH /deployments/{id}`` body; ``set_file=""`` clears the set."""
    body: dict[str, Any] = {}
    if enabled is not None:
        body["enabled"] = enabled
    if set_file is not None:
        body["set"] = set_file
    if not body:
        raise ToolArgumentError("nothing to change: pass enabled and/or set_file")
    return body


def _webrequest_changes(
    urls: list[str] | None,
    add: list[str] | None,
    remove: list[str] | None,
) -> dict[str, Any]:
    """The ``PUT /webrequest`` body: a full ``urls`` replace or an add/remove
    edit, never both."""
    is_edit = add is not None or remove is not None
    if urls is not None and is_edit:
        raise ToolArgumentError("pass either urls, or add/remove, not both")
    if urls is not None:
        return {"urls": urls}
    if not is_edit:
        raise ToolArgumentError("pass urls, or add and/or remove")
    body: dict[str, Any] = {}
    if add is not None:
        body["add"] = add
    if remove is not None:
        body["remove"] = remove
    return body


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
