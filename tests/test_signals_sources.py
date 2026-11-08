"""Telegram and websocket sources hand over Messages the same way."""

import asyncio
import json
from dataclasses import replace

import httpx

from signals.config import Settings
from signals.sources.telegram import TelegramHub, bare_peer_id
from signals.sources.websocket import frame_message
from signals.state import State
from tests.signals_helpers import rules_from


def _settings(tmp_path, rules, auth_key="fake-telethon-key"):
    return Settings(
        rules=rules,
        mt5_url="http://nginx",
        api_token="",
        telethon_url="http://telethon-plus:8080",
        telethon_auth_key=auth_key,
        state_path=str(tmp_path / "state.sqlite"),
        heartbeat_path=str(tmp_path / "heartbeat"),
        log_level="info",
    )


class FakeTelethon:
    CHATS = {"@test_signal_channel": 1234567890, "@other_signal_channel": 222}

    def __init__(self):
        self.messages = {"@test_signal_channel": [], "@other_signal_channel": []}
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        chat = request.url.params.get("chat")
        if request.url.path == "/api/entities":
            return httpx.Response(200, json={"id": self.CHATS[chat], "type": "Channel"})
        if request.url.path == "/api/messages":
            # newest first, like telethon-plus
            return httpx.Response(200, json=list(reversed(self.messages[chat])))
        return httpx.Response(404, json={"detail": "not found"})


def _hub(tmp_path, telethon=None):
    rules = rules_from(tmp_path)
    telethon = telethon or FakeTelethon()
    state = State(str(tmp_path / "state.sqlite"))
    received = []

    async def emit(message):
        received.append(message)
        state.mark_seen(message.source, message.id)

    hub = TelegramHub(
        _settings(tmp_path, rules),
        list(rules.sources.values()),
        state,
        emit,
        transport=httpx.MockTransport(telethon.handler),
    )
    return hub, telethon, state, received


def _item(message_id, text="TESTUSD BUY"):
    return {"id": message_id, "date": "2026-01-01T00:00:00+00:00", "text": text, "chat_id": 1234567890}


def test_bare_peer_id_strips_telethon_marking():
    assert bare_peer_id(-1001234567890) == 1234567890
    assert bare_peer_id(-222) == 222
    assert bare_peer_id(333) == 333
    assert bare_peer_id("nope") is None


def test_the_hub_sends_its_auth_key_and_builds_the_websocket_url(tmp_path):
    hub, telethon, _, _ = _hub(tmp_path)
    asyncio.run(hub.resolve())
    assert telethon.requests[0].headers["authorization"] == "Bearer fake-telethon-key"
    assert hub.websocket_url() == "ws://telethon-plus:8080/ws/updates?token=fake-telethon-key"


def test_a_first_start_records_the_backlog_without_trading_it(tmp_path):
    hub, telethon, state, received = _hub(tmp_path)
    telethon.messages["@test_signal_channel"] = [_item(1), _item(2)]
    source = hub._sources[0]
    asyncio.run(hub.poll(source))
    assert received == []
    assert state.is_seen("testchan", "1") and state.is_seen("testchan", "2")


def test_later_polls_hand_over_new_messages_oldest_first(tmp_path):
    hub, telethon, state, received = _hub(tmp_path)
    source = hub._sources[0]
    telethon.messages["@test_signal_channel"] = [_item(1)]
    asyncio.run(hub.poll(source))
    telethon.messages["@test_signal_channel"] = [_item(1), _item(3), _item(2)]
    asyncio.run(hub.poll(source))
    assert [m.id for m in received] == ["1", "2", "3"]
    assert received[1].source == "testchan"


def test_live_events_are_routed_by_chat(tmp_path):
    hub, _, _, received = _hub(tmp_path)
    asyncio.run(hub.resolve())

    def event(chat_id, kind="NewMessage"):
        return json.dumps(
            {
                "type": kind,
                "chat_id": chat_id,
                "message": {"id": 77, "date": "2026-01-01T00:00:00+00:00", "text": "TESTUSD BUY"},
            }
        )

    asyncio.run(hub.on_event(event(-1001234567890)))
    asyncio.run(hub.on_event(event(-222)))
    asyncio.run(hub.on_event(event(-1009999999999)))
    asyncio.run(hub.on_event(event(-1001234567890, kind="MessageEdited")))
    asyncio.run(hub.on_event("not json"))
    assert [(m.source, m.id) for m in received] == [("testchan", "77"), ("otherchan", "77")]


def test_a_failed_poll_hands_over_nothing(tmp_path):
    def handler(request):
        return httpx.Response(502, json={"detail": "telegram error"})

    hub, _, _, received = _hub(tmp_path)
    hub._http = httpx.AsyncClient(base_url="http://telethon-plus:8080", transport=httpx.MockTransport(handler))
    asyncio.run(hub.poll(hub._sources[0]))
    assert received == []


def _ws_source(tmp_path, **changes):
    base = rules_from(tmp_path).sources["testchan"]
    return replace(base, **{"type": "websocket", "url": "wss://feed.example.invalid", **changes})


def test_a_plain_text_frame_is_the_message(tmp_path):
    message = frame_message(_ws_source(tmp_path), "TESTUSD BUY")
    assert message.text == "TESTUSD BUY"
    assert len(message.id) == 10


def test_a_json_frame_takes_its_text_and_id_fields(tmp_path):
    source = _ws_source(tmp_path, text_field="message", id_field="id")
    message = frame_message(source, json.dumps({"id": 4242, "message": "TESTUSD SELL"}))
    assert (message.id, message.text) == ("4242", "TESTUSD SELL")


def test_an_id_that_does_not_fit_an_order_id_is_hashed(tmp_path):
    source = _ws_source(tmp_path, text_field="message", id_field="id")
    first = frame_message(source, json.dumps({"id": "a-very-long-feed-id-0001", "message": "x"}))
    second = frame_message(source, json.dumps({"id": "a-very-long-feed-id-0001", "message": "x"}))
    assert first.id == second.id
    assert len(first.id) == 10


def test_a_json_frame_without_its_text_field_is_dropped(tmp_path):
    source = _ws_source(tmp_path, text_field="message")
    assert frame_message(source, json.dumps({"body": "TESTUSD BUY"})) is None
    assert frame_message(source, "not json") is None


def test_a_channel_empty_at_startup_trades_its_first_polled_message(tmp_path):
    """Regression: the first-start check asked the state on every poll, so a
    channel with no messages at startup stayed in first-start mode and its
    next polled message was recorded as seen instead of traded."""
    hub, telethon, _, received = _hub(tmp_path)
    source = hub._sources[0]
    asyncio.run(hub.poll(source))
    telethon.messages["@test_signal_channel"] = [_item(5)]
    asyncio.run(hub.poll(source))
    assert [m.id for m in received] == ["5"]


def test_the_websocket_source_reads_a_live_feed(tmp_path):
    from websockets.asyncio.server import serve

    from signals.sources.websocket import WebsocketSource

    async def scenario():
        headers_seen = []

        async def feed(connection):
            headers_seen.append(connection.request.headers.get("X-Key"))
            await connection.send(json.dumps({"id": 1, "message": "TESTUSD BUY"}))
            await connection.send(json.dumps({"id": 2, "message": "TESTUSD SELL"}))
            await connection.wait_closed()

        received = []
        async with serve(feed, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            source = _ws_source(
                tmp_path,
                url=f"ws://127.0.0.1:{port}",
                headers={"X-Key": "fake-feed-key"},
                text_field="message",
                id_field="id",
            )

            async def emit(message):
                received.append(message)

            task = asyncio.create_task(WebsocketSource(source, emit).run())
            while len(received) < 2:
                await asyncio.sleep(0.01)
            task.cancel()
        assert headers_seen == ["fake-feed-key"]
        assert [(m.id, m.text) for m in received] == [("1", "TESTUSD BUY"), ("2", "TESTUSD SELL")]

    asyncio.run(asyncio.wait_for(scenario(), 10))
