"""The Telegram path end to end, offline: the real TelegramHub and Engine
against a mock telethon-plus on a local socket (tests/mock_telethon.py) and a
fake terminal. A message posted to the channel must come out as orders, once,
whether it arrives over the websocket or only through a catch-up poll."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from signals.config import Settings
from signals.engine import Engine
from signals.sources.telegram import TelegramHub
from signals.state import State
from signals.terminal import TerminalClient
from tests.mock_telethon import MockTelethonPlus
from tests.signals_helpers import SIGNAL_TEXT, FakeTerminal, rules_from

AUTH_KEY = "fake-telethon-key"
CHATS = {"@test_signal_channel": 1234567890, "@other_signal_channel": 222}


class Stack:
    """Mock telethon-plus, fake terminal, and the copier running on them."""

    def __init__(self, tmp_path, auth_key: str = AUTH_KEY, poll_seconds: float | None = None) -> None:
        self.telethon = MockTelethonPlus(CHATS, auth_key=AUTH_KEY)
        self.terminal = FakeTerminal()
        self.state = State(str(tmp_path / "state.sqlite"))
        self.rules = rules_from(tmp_path)
        if poll_seconds is not None:
            sources = {
                name: replace(source, poll_seconds=poll_seconds) for name, source in self.rules.sources.items()
            }
            self.rules = replace(self.rules, sources=sources)
        self.auth_key = auth_key
        self.tmp_path = tmp_path
        self.hub: TelegramHub | None = None
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> Stack:
        await self.telethon.start()
        settings = Settings(
            rules=self.rules,
            mt5_url="http://nginx",
            api_token="fake-token",
            telethon_url=self.telethon.url,
            telethon_auth_key=self.auth_key,
            state_path=str(self.tmp_path / "state.sqlite"),
            heartbeat_path=str(self.tmp_path / "heartbeat"),
            log_level="info",
        )
        client = TerminalClient("http://nginx", "fake-token", transport=self.terminal.transport())
        engine = Engine(self.rules, client, self.state)
        self.hub = TelegramHub(settings, list(self.rules.sources.values()), self.state, engine.handle)
        self.task = asyncio.create_task(self.hub.run())
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.task is not None and self.hub is not None
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task
        await self.hub.aclose()
        await self.telethon.stop()
        self.state.close()

    async def orders_for(self, message_id: int, legs: int = 3, timeout: float = 5.0) -> list[dict]:
        prefix = f"testchan:{message_id}:"

        async def _wait() -> list[dict]:
            while True:
                found = [order for _, order in self.terminal.orders if order["client_order_id"].startswith(prefix)]
                if len(found) >= legs:
                    return found
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(_wait(), timeout)


def _run(coro):
    asyncio.run(asyncio.wait_for(coro, 20))


def test_a_live_message_becomes_orders(tmp_path):
    async def scenario():
        async with Stack(tmp_path) as stack:
            await stack.telethon.wait_for_websocket()
            message_id = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT)
            orders = await stack.orders_for(message_id)
            assert [o["client_order_id"] for o in orders] == [
                f"testchan:{message_id}:1",
                f"testchan:{message_id}:2",
                f"testchan:{message_id}:3",
            ]
            assert {o["magic"] for o in orders} == {900001}
            assert "/api/entities" in stack.telethon.requests

    _run(scenario())


def test_the_backlog_on_a_first_start_is_not_traded(tmp_path):
    async def scenario():
        stack = Stack(tmp_path)
        chat_id = CHATS["@test_signal_channel"]
        stack.telethon.messages[chat_id] = [
            {"id": 90, "date": "2026-01-01T00:00:00+00:00", "chat_id": chat_id, "text": SIGNAL_TEXT}
        ]
        async with stack:
            await stack.telethon.wait_for_websocket()
            new = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT)
            await stack.orders_for(new)
            assert not any(o["client_order_id"].startswith("testchan:90:") for _, o in stack.terminal.orders)
            assert stack.state.is_seen("testchan", "90")

    _run(scenario())


def test_a_message_the_websocket_missed_is_caught_by_the_poll(tmp_path):
    async def scenario():
        async with Stack(tmp_path, poll_seconds=0.2) as stack:
            await stack.telethon.wait_for_websocket()
            # telethon-plus drops a subscriber whose buffer fills; the message
            # then reaches only the history the poll reads.
            message_id = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT, push=False)
            await stack.orders_for(message_id)

    _run(scenario())


def test_a_reconnect_polls_what_was_posted_while_down(tmp_path):
    async def scenario():
        async with Stack(tmp_path) as stack:
            await stack.telethon.wait_for_websocket()
            await stack.telethon.drop_websockets()
            message_id = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT, push=False)
            await stack.telethon.wait_for_websocket(connections=2)
            await stack.orders_for(message_id)

    _run(scenario())


def test_a_message_seen_on_both_paths_trades_once(tmp_path):
    async def scenario():
        async with Stack(tmp_path, poll_seconds=0.05) as stack:
            await stack.telethon.wait_for_websocket()
            message_id = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT)
            await stack.orders_for(message_id)
            await asyncio.sleep(0.3)
            prefix = f"testchan:{message_id}:"
            sent = [o for _, o in stack.terminal.orders if o["client_order_id"].startswith(prefix)]
            assert len(sent) == 3

    _run(scenario())


def test_other_chats_and_non_signals_send_nothing(tmp_path):
    async def scenario():
        async with Stack(tmp_path) as stack:
            await stack.telethon.wait_for_websocket()
            # otherchan has no target in the test rules.
            await stack.telethon.post("@other_signal_channel", SIGNAL_TEXT)
            chatter = await stack.telethon.post("@test_signal_channel", "Good morning, signals at 9")
            signal = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT)
            await stack.orders_for(signal)
            assert all(o["client_order_id"].startswith(f"testchan:{signal}:") for _, o in stack.terminal.orders)
            assert stack.state.is_seen("testchan", str(chatter))

    _run(scenario())


def test_a_wrong_auth_key_never_connects_and_never_trades(tmp_path):
    async def scenario():
        async with Stack(tmp_path, auth_key="wrong-key") as stack:
            await asyncio.sleep(0.5)
            assert stack.telethon.connections == 0
            assert stack.terminal.orders == []

    _run(scenario())


def test_a_refused_websocket_is_retried_and_the_poll_still_trades(tmp_path):
    """telethon-plus refuses a wrong /ws/updates token at the handshake with
    403. The copier must treat that like any disconnect: keep retrying, and
    keep trading what the catch-up poll finds."""

    async def scenario():
        stack = Stack(tmp_path, poll_seconds=0.2)
        stack.telethon.ws_auth_key = "another-fake-key"
        async with stack:
            await asyncio.sleep(0.3)
            message_id = await stack.telethon.post("@test_signal_channel", SIGNAL_TEXT, push=False)
            orders = await stack.orders_for(message_id)
            assert len(orders) == 3
            deadline = asyncio.get_running_loop().time() + 5
            while stack.telethon.refused_websockets < 2:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.05)
            assert stack.telethon.connections == 0
            assert stack.task is not None and not stack.task.done()

    _run(scenario())
