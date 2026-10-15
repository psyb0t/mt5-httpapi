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
  - Chart Deployments, only when chartctl is enabled on this terminal:
      artifacts:   ``list_experts``, ``upload_expert``, ``delete_expert``,
                   ``list_sets``, ``get_set``, ``upload_set``, ``delete_set``
      deployments: ``list_deployments``, ``get_deployment``,
                   ``create_deployment``, ``update_deployment``,
                   ``delete_deployment``, ``reconcile_deployments``
      charts:      ``list_charts``, ``get_loader``, ``screenshot_chart``,
                   ``close_chart``
      WebRequest:  ``get_webrequest``, ``set_webrequest``,
                   ``apply_webrequest``, ``verify_webrequest``
  - Files, only when the file API is enabled on this terminal:
                   ``list_files``, ``get_file``, ``put_file``, ``delete_file``
  - Escape hatches   — ``request`` (JSON routes) and ``endpoints`` (route catalog)

File uploads (``upload_expert``, ``upload_set``) take the file content as
base64 and send it as the same multipart form the REST API expects.
``screenshot_chart`` returns the PNG as MCP image content.

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
import base64
import binascii
import codecs
import hashlib
import io
import json
import logging
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from mt5api.config import API_TOKEN, CHARTCTL_ENABLED, FILES_ENABLED
from mt5api.chartctl.command import (
    SCREENSHOT_DEFAULT_HEIGHT,
    SCREENSHOT_DEFAULT_WIDTH,
)

logger = logging.getLogger(__name__)

_SKIP_METHODS = frozenset({"HEAD", "OPTIONS"})

# Live-account safety note appended to every destructive tool's docstring.
_LIVE_NOTE = (
    "This is an irreversible action on a live trading account — only call it "
    "when the user explicitly asked for that specific action."
)

# Multipart form field names the chartctl upload handlers read.
_EXPERT_FORM_FIELD = "expert"
_SET_FORM_FIELD = "set"
_MULTIPART_FORM_DATA = "multipart/form-data"
_PNG_MIME_TYPE = "image/png"
_PNG_IMAGE_FORMAT = "png"
_HTTP_OK = 200
_RUNAS_QUERY = {"runas": "1"}
# Path-segment values that would address a different route than the one named.
_RELATIVE_PATH_SEGMENTS = frozenset({"", ".", ".."})
_PATH_SEPARATORS = ("/", "\\")

# File API: the route prefix of each tree, the multipart field it reads, and
# the largest file get_file returns inline (the /mcp response is JSON, and
# base64 grows the bytes by a third).
_FILE_TREE_PREFIXES = {"terminal": "/files", "compile": "/compile/files"}
_FILE_FORM_FIELD = "file"
_FILE_MAX_INLINE_BYTES = 16 * 1024 * 1024
_JSON_MIME_TYPE = "application/json"
_DOT_SEGMENTS = frozenset({".", ".."})
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_FLAG_ON = "1"

_INSTRUCTIONS = (
    "HTTP interface to a MetaTrader 5 terminal, exposed over MCP as "
    "dedicated typed tools grouped by family: health/terminal "
    "(ping, get_terminal, terminal_control), account (get_account), "
    "market data (list_symbols, get_symbol, get_tick, get_rates, "
    "get_ticks, get_rates_ta), positions (list_positions, "
    "get_position, modify_position, close_position), orders "
    "(list_orders, get_order, create_order, modify_order, "
    "cancel_order), history (get_history_orders, get_history_deals) "
    "and backtests (get_backtest polls a job; new runs are multipart, "
    "submit via the REST API). `request` and "
    "`endpoints` remain as an escape hatch for routes without a "
    "dedicated tool. Placing, modifying or cancelling orders and "
    "modifying or closing positions are real, irreversible actions "
    "on a live trading account with no client-side retry. Only "
    "call those tools when the user explicitly asked for that "
    "specific action, and confirm the parameters first."
)

_CHARTCTL_INSTRUCTIONS = (
    " Chart Deployments are enabled on this terminal: stage EA files "
    "(upload_expert, upload_set, list_experts, list_sets, get_set, "
    "delete_expert, delete_set), declare deployments that a loader EA attaches to "
    "charts (create_deployment, list_deployments, get_deployment, "
    "update_deployment, delete_deployment, reconcile_deployments), inspect "
    "and capture charts (list_charts, get_loader, screenshot_chart, "
    "close_chart), and manage the WebRequest URL allowlist "
    "(get_webrequest, set_webrequest, apply_webrequest, verify_webrequest). A running "
    "deployment is a live EA that can trade on this account, so create, "
    "enable, re-point or delete one only when the user asked for it."
)

_FILES_INSTRUCTIONS = (
    " The file API is enabled on this terminal: list_files, get_file, "
    "put_file (put_file with extract unpacks a zip) and delete_file work on "
    "the terminal's install directory (tree 'terminal': MQL5/Include, "
    "MQL5/Libraries, MQL5/Files, logs, ...) or on the MQL5 tree compile "
    "builds against (tree 'compile'). A file written to MQL5/Experts, "
    "MQL5/Libraries or MQL5/Include changes what an expert on this account "
    "runs, so only write or delete there when the user asked for it."
)


def build_mcp_server(
    chartctl_enabled: bool = CHARTCTL_ENABLED,
    files_enabled: bool = FILES_ENABLED,
) -> FastMCP:
    """Construct the FastMCP server mounted (via a2wsgi) under ``/mcp``.

    The Chart Deployments tools are registered only when ``chartctl_enabled``
    and the file tools only when ``files_enabled``, matching server.py, which
    registers their REST routes under the same flags.
    """
    instructions = _INSTRUCTIONS
    if chartctl_enabled:
        instructions += _CHARTCTL_INSTRUCTIONS
    if files_enabled:
        instructions += _FILES_INSTRUCTIONS
    mcp = FastMCP(
        name="mt5-httpapi",
        instructions=instructions,
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

    if chartctl_enabled:
        _register_chartctl_tools(mcp)
    if files_enabled:
        _register_files_tools(mcp)

    # ── Escape hatches ───────────────────────────────────────────────

    @mcp.tool()
    async def endpoints() -> dict[str, Any]:
        """List every REST endpoint (method, path) from the Flask URL map —
        the catalog of routes ``request`` can call. Discover routes here
        rather than guessing paths."""
        found: list[dict[str, str]] = []
        for rule in _flask_app().url_map.iter_rules():
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
        ``/backtest/build-ini`` or ``/backtest/build-set``). It cannot send a
        multipart upload: stage chart files with ``upload_expert`` /
        ``upload_set``, and submit ``POST /backtest`` through the REST API
        directly.

        ``method``: GET / POST / PUT / PATCH / DELETE. ``path``: a full route
        from ``endpoints``, e.g. ``/account`` or ``/orders``. ``query``: URL
        query params. ``body``: JSON body for POST / PUT / PATCH. The call runs the
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


def _register_chartctl_tools(mcp: FastMCP) -> None:
    """Register the Chart Deployments and WebRequest tools on ``mcp``."""

    # ── Chart Deployments: artifacts ─────────────────────────────────

    @mcp.tool()
    async def list_experts() -> dict[str, Any]:
        """List staged expert ``.ex5`` files with size and sha256, both
        uploaded and host-managed (``GET /experts``)."""
        return await _call("GET", "/experts")

    @mcp.tool()
    async def upload_expert(
        filename: str,
        content_base64: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Stage a compiled expert for deployment (``POST /experts``).

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
        return await _call("POST", "/experts", query=query, files=files)

    @mcp.tool()
    async def delete_expert(name: str) -> dict[str, Any]:
        """Remove a staged expert ``.ex5`` (``DELETE /experts/{name}``).
        Refused with 409 while a deployment uses it, and with 403 for a
        host-managed file that was never deployed."""
        segment = _path_segment("name", name)
        return await _call("DELETE", f"/experts/{segment}")

    @mcp.tool()
    async def list_sets() -> dict[str, Any]:
        """List staged ``.set`` parameter files, uploaded and host-managed
        (``GET /sets``)."""
        return await _call("GET", "/sets")

    @mcp.tool()
    async def get_set(name: str) -> dict[str, Any]:
        """Get one staged ``.set`` file parsed into its inputs
        (``GET /sets/{name}``)."""
        segment = _path_segment("name", name)
        return await _call("GET", f"/sets/{segment}")

    @mcp.tool()
    async def delete_set(name: str) -> dict[str, Any]:
        """Remove a staged ``.set`` file (``DELETE /sets/{name}``).
        Refused with 409 while a deployment uses it, and with 403 for a
        host-managed file."""
        segment = _path_segment("name", name)
        return await _call("DELETE", f"/sets/{segment}")

    @mcp.tool()
    async def upload_set(
        filename: str,
        content: str = "",
        content_base64: str = "",
    ) -> dict[str, Any]:
        """Stage a ``.set`` parameter file (``POST /sets``) and get its
        parsed inputs back. Replaces a staged file of the same name.

        Pass exactly one of ``content`` (the file as text, ``Name=value``
        per line) or ``content_base64`` (the raw bytes, for a UTF-16 file
        exported by MT5). Check the returned ``inputs``: a file with no
        ``Name=value`` lines parses to none, and an expert deployed with it
        runs on its default inputs.
        """
        data = _set_file_bytes(content, content_base64)
        files = {_SET_FORM_FIELD: (filename, data)}
        return await _call("POST", "/sets", files=files)

    # ── Chart Deployments: deployments ───────────────────────────────

    @mcp.tool()
    async def list_deployments() -> dict[str, Any]:
        """List every deployment merged with what the loader EA observes on
        the terminal: status, chart id, errors, and whether the terminal has
        converged on the desired state (``GET /deployments``)."""
        return await _call("GET", "/deployments")

    @mcp.tool()
    async def get_deployment(deployment_id: str) -> dict[str, Any]:
        """Get one deployment and its observed status
        (``GET /deployments/{deployment_id}``)."""
        segment = _path_segment("deployment_id", deployment_id)
        return await _call("GET", f"/deployments/{segment}")

    @mcp.tool()
    async def create_deployment(
        expert: str,
        symbol: str,
        timeframe: str,
        set_file: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Declare a deployment: run a staged expert on a chart
        (``POST /deployments``). The loader EA opens the chart and attaches
        the expert on its next pass. Poll ``get_deployment`` until the
        status is ``running``; stop polling on ``failed`` or ``degraded``
        and report its ``error``, and check ``get_loader`` if it stays
        ``pending`` (nothing attaches while the loader is not alive).

        ``expert``: a staged ``.ex5`` name. ``symbol``: the broker's symbol
        name. ``timeframe``: one of M1/M2/M3/M4/M5/M6/M10/M12/M15/M20/M30/
        H1/H2/H3/H4/H6/H8/H12/D1/W1/MN1. ``set_file``: optional staged
        ``.set`` name for the expert's inputs. ``enabled``: false creates it
        paused. Creating an enabled deployment for a symbol/timeframe pair
        that another enabled deployment already targets is refused with 409
        ``DUPLICATE_CHART``.

        DESTRUCTIVE: an enabled deployment is a live EA that can trade on
        this account. Only call on explicit user request.
        """
        body: dict[str, Any] = {
            "expert": expert,
            "symbol": symbol,
            "timeframe": timeframe,
            "enabled": enabled,
        }
        if set_file:
            body["set"] = set_file
        return await _call("POST", "/deployments", body=body)

    @mcp.tool()
    async def update_deployment(
        deployment_id: str,
        enabled: bool | None = None,
        set_file: str | None = None,
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

        DESTRUCTIVE: changes what a live EA does on this account. Only call
        on explicit user request.
        """
        body = _deployment_changes(enabled, set_file)
        segment = _path_segment("deployment_id", deployment_id)
        return await _call("PATCH", f"/deployments/{segment}", body=body)

    @mcp.tool()
    async def delete_deployment(deployment_id: str) -> dict[str, Any]:
        """Delete a deployment; the loader EA closes its chart
        (``DELETE /deployments/{deployment_id}``).

        DESTRUCTIVE: stops a live EA on this account. Only call on explicit
        user request.
        """
        segment = _path_segment("deployment_id", deployment_id)
        return await _call("DELETE", f"/deployments/{segment}")

    @mcp.tool()
    async def reconcile_deployments() -> dict[str, Any]:
        """Bump the desired-state revision (``POST /deployments/reconcile``).
        The loader already reconciles on every pass, so this changes nothing
        by itself; compare the returned ``revision`` with ``get_loader``'s
        ``applied_revision`` to see when the loader has caught up."""
        return await _call("POST", "/deployments/reconcile")

    # ── Chart Deployments: charts and loader ─────────────────────────

    @mcp.tool()
    async def list_charts() -> dict[str, Any]:
        """List the charts open in the terminal with their symbol,
        timeframe, attached expert and owning deployment, as the loader EA
        last reported them (``GET /charts``)."""
        return await _call("GET", "/charts")

    @mcp.tool()
    async def get_loader() -> dict[str, Any]:
        """Get the loader EA's status: alive, version, and which desired
        revision it has applied (``GET /loader``). If ``alive`` is false,
        no deployment will change until the loader runs."""
        return await _call("GET", "/loader")

    @mcp.tool()
    async def screenshot_chart(
        chart_id: int,
        width: int = SCREENSHOT_DEFAULT_WIDTH,
        height: int = SCREENSHOT_DEFAULT_HEIGHT,
    ) -> Image:
        """Capture one chart as a PNG image
        (``POST /charts/{chart_id}/screenshot``). Take ``chart_id`` from
        ``list_charts`` or ``get_deployment``."""
        query = {"width": width, "height": height}
        status, content_type, data = await _call_bytes(
            "POST",
            f"/charts/{chart_id}/screenshot",
            query=query,
        )
        if status != _HTTP_OK or not content_type.startswith(_PNG_MIME_TYPE):
            detail = data.decode("utf-8", errors="replace")
            raise ToolError(f"screenshot failed: HTTP {status}: {detail}")
        return Image(data=data, format=_PNG_IMAGE_FORMAT)

    @mcp.tool()
    async def close_chart(chart_id: int) -> dict[str, Any]:
        """Close one chart by id, including charts no deployment owns
        (``POST /charts/{chart_id}/close``). The loader refuses to close its
        own chart. A chart that belongs to an enabled deployment is opened
        again on the loader's next pass; to stop that expert, pause or
        delete the deployment instead.

        DESTRUCTIVE: any expert on that chart stops. Only call on explicit
        user request.
        """
        return await _call("POST", f"/charts/{chart_id}/close")

    # ── WebRequest allowlist ─────────────────────────────────────────

    @mcp.tool()
    async def get_webrequest() -> dict[str, Any]:
        """Get the URLs this terminal's experts may call with
        ``WebRequest()`` (``GET /webrequest``)."""
        return await _call("GET", "/webrequest")

    @mcp.tool()
    async def set_webrequest(
        urls: list[str] | None = None,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        runas: bool = False,
    ) -> dict[str, Any]:
        """Change the ``WebRequest()`` URL allowlist and apply it now
        (``PUT /webrequest``).

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
        return await _call("PUT", "/webrequest", query=query, body=body)

    @mcp.tool()
    async def apply_webrequest(runas: bool = False) -> dict[str, Any]:
        """Re-apply the stored ``WebRequest()`` allowlist to the running
        terminal (``POST /webrequest/apply``). Inside the Windows VM the
        terminal forgets the list whenever it restarts. The API re-applies
        it when its process starts and after every terminal restart it
        performs itself, so call this after a restart from outside the API,
        or when ``get_webrequest`` shows the list but an expert's
        ``WebRequest()`` is still refused. ``runas``: as in
        ``set_webrequest``.

        A stored URL the terminal will not take is skipped and listed under
        ``rejected`` instead of failing the whole apply. On a bare-metal
        terminal this RESTARTS the terminal.
        """
        query = _RUNAS_QUERY if runas else None
        return await _call("POST", "/webrequest/apply", query=query)

    @mcp.tool()
    async def verify_webrequest() -> dict[str, Any]:
        """Read the ``WebRequest()`` allowlist the running terminal really
        holds, beside the stored one (``POST /webrequest/verify``). Inside the
        Windows VM only.

        It opens the terminal's Options dialog for a moment, so it answers
        429 when called again within 10 seconds and 409 while a backtest
        runs. Use it to check an apply, not to poll.
        """
        return await _call("POST", "/webrequest/verify")


def _register_files_tools(mcp: FastMCP) -> None:
    """Register the file API tools on ``mcp``."""

    @mcp.tool()
    async def list_files(path: str = "", tree: str = "terminal") -> dict[str, Any]:
        """List one directory of this terminal's files (``GET /files/{path}``).

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
        status, content_type, data = await _call_bytes("GET", url)
        _raise_for_file_status(status, data)
        if not content_type.startswith(_JSON_MIME_TYPE):
            raise ToolError(f"{path} is a file; use get_file to read it")
        return _decode_json_bytes(data)

    @mcp.tool()
    async def get_file(path: str, tree: str = "terminal") -> dict[str, Any]:
        """Read one file (``GET /files/{path}``).

        ``tree`` and ``path`` as in ``list_files``. Returns ``size``,
        ``sha256`` and the content: ``text`` for UTF-8 or UTF-16 text (MT5
        writes its logs as UTF-16), otherwise ``content_base64``. Files over
        16 MiB are refused; download those through the REST API.
        ``mt5start.ini`` and ``Config/accounts.dat`` hold the broker
        credentials and are never returned.
        """
        url = _file_url(tree, path)
        status, content_type, data = await _call_bytes("GET", url)
        _raise_for_file_status(status, data)
        if content_type.startswith(_JSON_MIME_TYPE):
            raise ToolError(f"{path} is a directory; use list_files")
        return _file_payload(path, data)

    @mcp.tool()
    async def put_file(
        path: str,
        content: str = "",
        content_base64: str = "",
        extract: bool = False,
        tree: str = "terminal",
    ) -> dict[str, Any]:
        """Create or replace one file, creating its directories
        (``PUT /files/{path}``).

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
        ``MQL5/Include`` changes what experts on this account run. Only do
        it on explicit user request.
        """
        data = _file_bytes(content, content_base64)
        url = _file_url(tree, path, allow_root=False)
        query = {"extract": _FLAG_ON} if extract else None
        filename = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        files = {_FILE_FORM_FIELD: (filename, data)}
        return await _call("PUT", url, query=query, files=files)

    @mcp.tool()
    async def delete_file(
        path: str,
        recursive: bool = False,
        tree: str = "terminal",
    ) -> dict[str, Any]:
        """Delete a file, or a directory (``DELETE /files/{path}``).

        A non-empty directory needs ``recursive`` true. The same paths
        ``put_file`` refuses cannot be deleted, nor a directory holding any
        of them. ``tree`` as in ``list_files``. Only on explicit user
        request.
        """
        url = _file_url(tree, path, allow_root=False)
        query = {"recursive": _FLAG_ON} if recursive else None
        return await _call("DELETE", url, query=query)


def _file_url(tree: str, path: str, allow_root: bool = True) -> str:
    """The file API URL for ``path`` in ``tree``, each segment encoded.

    Dot segments are refused here because an HTTP client would resolve
    them before the server could refuse them.
    """
    prefix = _FILE_TREE_PREFIXES.get(tree)
    if prefix is None:
        raise ToolError(f"tree must be one of {sorted(_FILE_TREE_PREFIXES)}, got {tree!r}")
    text = path.replace("\\", "/")
    if text.startswith("/"):
        raise ToolError(f"path {path!r} must be relative to the tree root")
    segments = [segment for segment in text.rstrip("/").split("/") if segment]
    if any(segment in _DOT_SEGMENTS for segment in segments):
        raise ToolError(f"path {path!r} must not contain '.' or '..' segments")
    if not segments:
        if not allow_root:
            raise ToolError("path is empty; name a file or directory")
        return prefix
    return prefix + "/" + "/".join(quote(segment, safe="") for segment in segments)


def _raise_for_file_status(status: int, data: bytes) -> None:
    if status == _HTTP_OK:
        return
    detail = data.decode("utf-8", errors="replace")
    raise ToolError(f"HTTP {status}: {detail}")


def _decode_json_bytes(data: bytes) -> dict[str, Any]:
    return json.loads(data.decode("utf-8"))


def _file_bytes(content: str, content_base64: str) -> bytes:
    """A file's bytes from exactly one of text or base64 content. Empty
    text is a valid (empty) file."""
    if content and content_base64:
        raise ToolError("pass either content or content_base64, not both")
    if content_base64:
        return _decode_base64("content_base64", content_base64)
    return content.encode("utf-8")


def _file_payload(path: str, data: bytes) -> dict[str, Any]:
    """The get_file result: metadata plus the content as text when it is
    text, base64 otherwise."""
    if len(data) > _FILE_MAX_INLINE_BYTES:
        raise ToolError(
            f"{path} is {len(data)} bytes; get_file returns at most "
            f"{_FILE_MAX_INLINE_BYTES}, download it through the REST API"
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


def _path_segment(field: str, value: str) -> str:
    """One URL path segment built from a tool argument, percent-encoded.

    A name such as ``../deployments/dep_x`` would otherwise reach a
    different route, and ``?`` or ``#`` would cut the path short.
    """
    has_separator = any(sep in value for sep in _PATH_SEPARATORS)
    if value in _RELATIVE_PATH_SEGMENTS or has_separator:
        raise ToolError(f"{field} must be a single name without slashes, got {value!r}")
    return quote(value, safe="")


def _decode_base64(field: str, value: str) -> bytes:
    """Decode a base64 tool argument, refusing empty or malformed input.
    Whitespace, such as the line breaks ``base64`` inserts, is ignored."""
    compact = "".join(value.split())
    if not compact:
        raise ToolError(f"{field} is empty")
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ToolError(f"{field} is not valid base64") from err


def _set_file_bytes(content: str, content_base64: str) -> bytes:
    """The ``.set`` bytes from exactly one of text or base64 content."""
    if bool(content) == bool(content_base64):
        raise ToolError("pass exactly one of content or content_base64")
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
        raise ToolError("nothing to change: pass enabled and/or set_file")
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
        raise ToolError("pass either urls, or add/remove, not both")
    if urls is not None:
        return {"urls": urls}
    if not is_edit:
        raise ToolError("pass urls, or add and/or remove")
    body: dict[str, Any] = {}
    if add is not None:
        body["add"] = add
    if remove is not None:
        body["remove"] = remove
    return body


def _flask_app() -> Any:
    """The mt5api Flask app. Imported late: server.py imports this module at
    load time, so a module-scope import would be circular."""
    from mt5api.server import app

    return app


async def _call(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    files: dict[str, tuple[str, bytes]] | None = None,
) -> dict[str, Any]:
    """Dispatch one REST call through the in-process WSGI helper off the
    event loop, logging the route only (never body/query, which can carry
    order params). ``files`` maps a form field to (filename, bytes) and sends
    a multipart form instead of a JSON body."""
    logger.info("mcp proxy request", extra={"http_method": method, "path": path})
    resp = await asyncio.to_thread(_call_wsgi, method, path, query, body, files)
    return {"status": resp.status_code, "body": _decode(resp)}


async def _call_bytes(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
) -> tuple[int, str, bytes]:
    """Like ``_call`` for a binary response: (status, content type, body)."""
    logger.info("mcp proxy request", extra={"http_method": method, "path": path})
    resp = await asyncio.to_thread(_call_wsgi, method, path, query, None, None)
    return resp.status_code, resp.content_type or "", resp.get_data()


def _call_wsgi(
    method: str,
    path: str,
    query: dict[str, Any] | None,
    body: dict[str, Any] | None,
    files: dict[str, tuple[str, bytes]] | None,
) -> Any:
    """Dispatch one request through the Flask app's WSGI test client in-process,
    injecting the configured bearer token so it clears the app's own auth."""
    headers: dict[str, str] = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"

    client = _flask_app().test_client()
    if files:
        form = {
            field: (io.BytesIO(data), filename)
            for field, (filename, data) in files.items()
        }
        return client.open(
            path,
            method=method,
            query_string=query or None,
            data=form,
            content_type=_MULTIPART_FORM_DATA,
            headers=headers,
        )
    return client.open(
        path,
        method=method,
        query_string=query or None,
        json=body if body is not None else None,
        headers=headers,
    )


def _decode(resp: Any) -> Any:
    """Return the response as parsed JSON, falling back to raw text for a
    non-JSON body (e.g. a Werkzeug HTML error page). ``get_json`` returns None
    for a non-JSON content type rather than raising, so a bare error page would
    otherwise surface as ``null`` instead of its text."""
    data = resp.get_json(silent=True)
    if data is not None:
        return data
    return {"raw": resp.get_data(as_text=True)}
