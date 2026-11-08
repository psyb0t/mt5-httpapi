"""create_order and modify_order carry `stoplimit` and `expiration` to the
order routes, on both MCP servers.

Same arrangement as tests/test_mcp_files_tools.py: the per-terminal server
dispatches in-process into the Flask app, the unified server goes over httpx
with a transport that hands each request to that same app. The orders handler
sits on the `patch_handler` recorder, so each test reads the request that would
have reached `order_send`.
"""

from __future__ import annotations

import asyncio
import json
from collections import namedtuple
from typing import Any

import MetaTrader5 as mt5
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import mt5api.mt5client as mt5client
from mt5api.handlers import orders as orders_handler
from tests.test_mcp_chartctl_tools import (
    ACCOUNT,
    BROKER,
    _content_blocks,
    _FlaskTransport,
    _REJECTION,
)

TICKET = 12345
EXPIRY_UTC = 1_800_000_000
BROKER_OFFSET = 10_800  # a broker three hours ahead of real UTC

OrderResult = namedtuple("OrderResult", "retcode deal order volume price comment")
Order = namedtuple(
    "Order",
    "ticket symbol volume_current price_open price_stoplimit sl tp type type_time "
    "time_expiration",
)


def _app():
    from mt5api.server import app

    app.config["TESTING"] = True
    return app


class _TerminalHarness:
    def __init__(self, monkeypatch) -> None:
        from mt5api import mcp_server

        app = _app()
        monkeypatch.setattr(mcp_server, "_flask_app", lambda: app)
        self.mcp = mcp_server.build_mcp_server(chartctl_enabled=False, files_enabled=False)

    async def call(self, tool: str, **args: Any) -> tuple[int, dict]:
        """(HTTP status, body): a per-terminal tool returns every status."""
        result = json.loads(_content_blocks(await self.mcp.call_tool(tool, args))[0].text)
        return result["status"], result["body"]


class _UnifiedHarness:
    def __init__(self) -> None:
        from mcpunifier.client import TerminalClient
        from mcpunifier.config import Settings, Terminal
        from mcpunifier.mcp_server import build_mcp_server

        terminal = Terminal(
            broker=BROKER,
            account=ACCOUNT,
            instance="default",
            port=6545,
            mode="live",
        )
        settings = Settings(
            terminals={terminal.key: terminal},
            mt5_host="mt5",
            api_token="",
            request_timeout=5.0,
            listen_host="127.0.0.1",
            listen_port=6600,
            log_level="info",
            log_file="/dev/null",
        )
        client = TerminalClient(settings, transport=_FlaskTransport(_app()))
        self.mcp = build_mcp_server(settings, client)

    async def call(self, tool: str, **args: Any) -> tuple[int, dict]:
        """(HTTP status, body): a unified tool raises on a non-2xx answer and
        does not report the status of a good one, so that is a plain 200."""
        routed = {"broker": BROKER, "account": ACCOUNT, **args}
        try:
            result = json.loads(_content_blocks(await self.mcp.call_tool(tool, routed))[0].text)
        except ToolError as err:
            match = _REJECTION.search(str(err))
            assert match, str(err)
            return int(match.group(1)), json.loads(match.group(2))
        result.pop("terminal")
        return 200, result


@pytest.fixture(params=["terminal", "unified"])
def server(request, monkeypatch):
    if request.param == "terminal":
        return _TerminalHarness(monkeypatch)
    return _UnifiedHarness()


@pytest.fixture
def orders(patch_handler, monkeypatch):
    recorder = patch_handler(orders_handler)
    monkeypatch.setattr(mt5client, "UTC_OFFSET_SECONDS", BROKER_OFFSET)
    monkeypatch.setattr(orders_handler, "_recent_client_orders", {})
    done = OrderResult(
        mt5.TRADE_RETCODE_DONE, deal=1, order=TICKET, volume=0.1, price=1.3, comment=""
    )
    recorder.set("order_send", done)
    recorder.set(
        "orders_get",
        [
            Order(
                ticket=TICKET,
                symbol="EURUSD",
                volume_current=0.1,
                price_open=1.3,
                price_stoplimit=1.29,
                sl=1.2,
                tp=1.4,
                type=mt5.ORDER_TYPE_BUY_STOP_LIMIT,
                type_time=mt5.ORDER_TIME_GTC,
                time_expiration=0,
            )
        ],
    )
    return recorder


def _run(coro):
    return asyncio.run(coro)


def test_create_order_sends_stoplimit_and_expiration(server, orders):
    async def scenario():
        return await server.call(
            "create_order",
            symbol="EURUSD",
            type="BUY_STOP_LIMIT",
            volume=0.1,
            price=1.3,
            stoplimit=1.29,
            expiration=EXPIRY_UTC,
        )

    status, _ = _run(scenario())

    assert status < 300
    sent = orders.sent_order()
    assert sent["type"] == mt5.ORDER_TYPE_BUY_STOP_LIMIT
    assert sent["stoplimit"] == 1.29
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET
    assert sent["type_time"] == mt5.ORDER_TIME_SPECIFIED


def test_create_order_without_them_sends_neither(server, orders):
    async def scenario():
        return await server.call(
            "create_order", symbol="EURUSD", type="BUY_LIMIT", volume=0.1, price=1.1
        )

    status, _ = _run(scenario())

    assert status < 300
    sent = orders.sent_order()
    assert "stoplimit" not in sent
    assert "expiration" not in sent
    assert "type_time" not in sent


def test_create_order_refuses_a_stop_limit_type_without_its_limit_price(server, orders):
    async def scenario():
        return await server.call(
            "create_order", symbol="EURUSD", type="SELL_STOP_LIMIT", volume=0.1, price=1.3
        )

    status, body = _run(scenario())

    assert status == 400
    assert "stoplimit" in body["error"]
    assert orders.calls_to("order_send") == []


def test_modify_order_sends_stoplimit_and_expiration(server, orders):
    async def scenario():
        return await server.call(
            "modify_order", ticket=TICKET, stoplimit=1.28, expiration=EXPIRY_UTC
        )

    status, _ = _run(scenario())

    assert status == 200
    sent = orders.sent_order()
    assert sent["stoplimit"] == 1.28
    assert sent["expiration"] == EXPIRY_UTC + BROKER_OFFSET
    assert sent["type_time"] == mt5.ORDER_TIME_SPECIFIED
    assert sent["price"] == 1.3


def test_modify_order_without_them_keeps_the_orders_own_values(server, orders):
    async def scenario():
        return await server.call("modify_order", ticket=TICKET, sl=1.25)

    status, _ = _run(scenario())

    assert status == 200
    sent = orders.sent_order()
    assert sent["sl"] == 1.25
    assert sent["stoplimit"] == 1.29
    assert sent["expiration"] == 0
