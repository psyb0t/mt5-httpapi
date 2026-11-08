"""Shared fixtures for the signal copier tests: a rules file builder and a
fake terminal behind httpx.MockTransport. Every value is made up (TST6)."""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from signals.config import Rules, load_rules
from signals.model import Message

TERMINALS = {"testbroker/demo/default", "otherbroker/demo/default"}

RULES_YAML = """
sources:
  testchan:
    type: telegram
    chat: "@test_signal_channel"
    parser: classic
    magic: 900001
  otherchan:
    type: telegram
    chat: "@other_signal_channel"
    parser: classic
    magic: 900002
    poll_seconds: 600

parsers:
  classic:
    open: '(?i)(?P<symbol>TESTUSD|TESTGOLD)\\s+(?P<side>buy|sell|long|short)\\s*@?\\s*(?P<entry>\\d+(?:[.,]\\d+)?)?(?:\\s*-\\s*(?P<entry2>\\d+(?:[.,]\\d+)?))?'
    sl: '(?i)\\bsl(?P<n>\\d)?\\b\\s*[:=@]?\\s*(?P<price>\\d+(?:[.,]\\d+)?)'
    tp: '(?i)\\btp(?P<n>\\d)?\\b\\s*[:=@]?\\s*(?P<price>\\d+(?:[.,]\\d+)?)'
    lots: '(?i)(?P<lots>\\d+(?:[.,]\\d+)?)\\s*lots?\\b'
    side_map: {long: buy, short: sell}

symbols:
  TESTGOLD: TESTUSD

targets:
  - source: testchan
    broker: testbroker
    account: demo
    sizing: {mode: multiplier, factor: 0.1, default_lots: 0.1}
    tp_split: [50, 30, 20]
"""

SYMBOL_INFO = {
    "name": "TESTUSD",
    "point": 0.01,
    "digits": 2,
    "trade_tick_size": 0.01,
    "trade_tick_value": 1.0,
    "volume_min": 0.01,
    "volume_max": 50.0,
    "volume_step": 0.01,
    "bid": 100.00,
    "ask": 100.10,
}

SIGNAL_TEXT = "TESTUSD BUY 100.00\nSL 99.00\nTP1 101.00\nTP2 102.00\nTP3 103.00\n1 lot"


def rules_from(tmp_path: Path, text: str = RULES_YAML, terminals: set[str] | None = None) -> Rules:
    path = tmp_path / "signals.yml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return load_rules(str(path), terminals if terminals is not None else TERMINALS)


def message(text: str = SIGNAL_TEXT, message_id: str = "101", source: str = "testchan") -> Message:
    return Message(source=source, id=message_id, time=datetime.now(timezone.utc), text=text)


class FakeTerminal:
    """Answers the routes the copier calls, per terminal path prefix, and
    records every order sent."""

    def __init__(self) -> None:
        self.symbols: dict[str, dict[str, Any]] = {"TESTUSD": dict(SYMBOL_INFO)}
        self.positions: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.account: dict[str, Any] = {"equity": 10000.0, "margin_mode": 2}
        self.orders: list[tuple[str, dict[str, Any]]] = []
        self.unreachable: set[str] = set()
        # client_order_id -> how POST /orders fails for that order only: None
        # drops the connection, an int answers with that HTTP status. A failed
        # order is not recorded in `orders`.
        self.failing_orders: dict[str, int | None] = {}
        self.order_status = 201
        self.next_ticket = 5000

    def handler(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        terminal = "/".join(parts[:3])
        route = "/" + "/".join(parts[3:])
        if terminal in self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        if request.method == "GET" and route.startswith("/symbols/"):
            info = self.symbols.get(route.removeprefix("/symbols/"))
            if info is None:
                return httpx.Response(404, json={"error": "symbol not found"})
            return httpx.Response(200, json=info)
        if request.method == "GET" and route == "/positions":
            return httpx.Response(200, json=self.positions)
        if request.method == "GET" and route == "/orders":
            return httpx.Response(200, json=self.pending)
        if request.method == "GET" and route == "/account":
            return httpx.Response(200, json=self.account)
        if request.method == "POST" and route == "/orders":
            body = json.loads(request.content)
            if body["client_order_id"] in self.failing_orders:
                failure = self.failing_orders[body["client_order_id"]]
                if failure is None:
                    raise httpx.ConnectError("connection refused", request=request)
                return httpx.Response(failure, json={"error": "order failed"})
            self.orders.append((terminal, body))
            self.next_ticket += 1
            if self.order_status != 201:
                return httpx.Response(200, json={"retcode": 10018, "comment": "Market closed"})
            return httpx.Response(201, json={"retcode": 10009, "order": self.next_ticket})
        return httpx.Response(404, json={"error": "no route"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
