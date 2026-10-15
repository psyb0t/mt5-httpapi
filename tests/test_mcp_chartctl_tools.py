"""Chart Deployments and WebRequest MCP tools, on both MCP servers.

Every scenario runs against the per-terminal server (mt5api/mcp_server.py) and
the unified server (mcpunifier/mcp_server.py), and both reach the real chartctl
handlers on the shared ``chartctl_app`` fixture. The per-terminal server goes
through its in-process WSGI client; the unifier goes through httpx with a
transport that forwards each request to the same Flask app. So the multipart
uploads and the PNG screenshots cross a real request/response boundary on both
paths, and a tool whose arguments map to the wrong request fails here.

tests.chartctl_fake_loader.FakeLoader plays the loader EA.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import re
import threading
from typing import Any

import httpx
import pytest
from flask import Flask
from mcp.server.fastmcp.exceptions import ToolError

from tests.chartctl_fake_loader import FakeLoader

BROKER = "ftmo"
ACCOUNT = "live1"
TERMINAL_KEY = f"{BROKER}/{ACCOUNT}/default"

EXPERT_BYTES = b"MZ\x00\x01fake-ex5-body"
SET_TEXT = "Lots=0.10\nMagic=777\n"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
LOADER_POLL_SECONDS = 0.02

CHARTCTL_TOOLS = frozenset({
    "list_experts", "upload_expert", "delete_expert",
    "list_sets", "get_set", "upload_set", "delete_set",
    "list_deployments", "get_deployment", "create_deployment",
    "update_deployment", "delete_deployment", "reconcile_deployments",
    "list_charts", "get_loader", "screenshot_chart", "close_chart",
    "get_webrequest", "set_webrequest", "apply_webrequest", "verify_webrequest",
})

_REJECTION = re.compile(r"returned HTTP (\d+): (.*)$", re.DOTALL)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _content_blocks(result) -> list:
    """FastMCP.call_tool returns (content, structured) for a tool with a
    structured return type, and the content alone otherwise."""
    if isinstance(result, tuple):
        return list(result[0])
    return list(result)


# ── Harnesses: one calling convention over both servers ──────────────


class _TerminalHarness:
    """The per-terminal server, dispatching in-process into chartctl_app."""

    def __init__(self, monkeypatch, app: Flask) -> None:
        from mt5api import mcp_server

        self.requests = 0
        real_call_wsgi = mcp_server._call_wsgi

        def counting_call_wsgi(*args):
            self.requests += 1
            return real_call_wsgi(*args)

        monkeypatch.setattr(mcp_server, "_flask_app", lambda: app)
        monkeypatch.setattr(mcp_server, "_call_wsgi", counting_call_wsgi)
        self.mcp = mcp_server.build_mcp_server(chartctl_enabled=True)

    async def content(self, tool: str, **args: Any) -> list:
        return _content_blocks(await self.mcp.call_tool(tool, args))

    async def _result(self, tool: str, **args: Any) -> dict:
        return json.loads((await self.content(tool, **args))[0].text)

    async def ok(self, tool: str, **args: Any) -> dict:
        result = await self._result(tool, **args)
        assert result["status"] < 400, result
        return result["body"]

    async def rejected(self, tool: str, **args: Any) -> tuple[int, dict]:
        result = await self._result(tool, **args)
        assert result["status"] >= 400, result
        return result["status"], result["body"]


class _FlaskTransport(httpx.AsyncBaseTransport):
    """Hands each httpx request to a Flask app, unchanged on the wire."""

    def __init__(self, app: Flask) -> None:
        self._client = app.test_client()
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.requests.append(request)
        # raw_path is the path and query exactly as sent, percent-encoding
        # intact, which is what a real server would parse.
        resp = self._client.open(
            request.url.raw_path.decode("ascii"),
            method=request.method,
            data=body,
            content_type=request.headers.get("content-type"),
        )
        return httpx.Response(
            resp.status_code,
            headers={"content-type": resp.content_type or ""},
            content=resp.get_data(),
        )


def _unified_server(
    transport: httpx.AsyncBaseTransport,
    chartctl: bool = True,
    api_token: str = "",
):
    from mcpunifier.client import TerminalClient
    from mcpunifier.config import Settings, Terminal
    from mcpunifier.mcp_server import build_mcp_server

    terminal = Terminal(
        broker=BROKER,
        account=ACCOUNT,
        instance="default",
        port=6545,
        mode="live",
        chartctl=chartctl,
    )
    settings = Settings(
        terminals={terminal.key: terminal},
        mt5_host="mt5",
        api_token=api_token,
        request_timeout=5.0,
        listen_host="127.0.0.1",
        listen_port=6600,
        log_level="info",
        log_file="/dev/null",
    )
    return build_mcp_server(settings, TerminalClient(settings, transport=transport))


class _UnifiedHarness:
    """The unified server, reaching chartctl_app over httpx."""

    def __init__(self, app: Flask) -> None:
        self._transport = _FlaskTransport(app)
        self.mcp = _unified_server(self._transport)

    @property
    def requests(self) -> int:
        return len(self._transport.requests)

    async def content(self, tool: str, **args: Any) -> list:
        routed = {"broker": BROKER, "account": ACCOUNT, **args}
        return _content_blocks(await self.mcp.call_tool(tool, routed))

    async def ok(self, tool: str, **args: Any) -> dict:
        result = json.loads((await self.content(tool, **args))[0].text)
        assert result.pop("terminal") == TERMINAL_KEY
        return result

    async def rejected(self, tool: str, **args: Any) -> tuple[int, dict]:
        with pytest.raises(ToolError) as excinfo:
            await self.content(tool, **args)
        match = _REJECTION.search(str(excinfo.value))
        assert match, str(excinfo.value)
        return int(match.group(1)), json.loads(match.group(2))


@pytest.fixture(params=["terminal", "unified"])
def server(request, monkeypatch, chartctl_app):
    if request.param == "terminal":
        return _TerminalHarness(monkeypatch, chartctl_app)
    return _UnifiedHarness(chartctl_app)


@contextlib.contextmanager
def _loader_answering_commands(proto_dir: str):
    """Run a FakeLoader that answers the command channel until the block ends."""
    loader = FakeLoader(proto_dir)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            loader.handle_command()
            stop.wait(LOADER_POLL_SECONDS)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield loader
    finally:
        stop.set()
        thread.join()


def _run(coro):
    return asyncio.run(coro)


# ── Artifacts ─────────────────────────────────────────────────────────


def test_upload_expert_stages_the_bytes_and_honours_overwrite(server):
    async def scenario():
        staged = await server.ok(
            "upload_expert", filename="EA.ex5", content_base64=_b64(EXPERT_BYTES)
        )
        assert staged["sha256"] == hashlib.sha256(EXPERT_BYTES).hexdigest()
        assert staged["size"] == len(EXPERT_BYTES)

        again = await server.ok(
            "upload_expert", filename="EA.ex5", content_base64=_b64(EXPERT_BYTES)
        )
        assert again["skipped"] is True

        changed = EXPERT_BYTES + b"-v2"
        status, body = await server.rejected(
            "upload_expert", filename="EA.ex5", content_base64=_b64(changed)
        )
        assert (status, body["code"]) == (409, "EXISTS")

        replaced = await server.ok(
            "upload_expert",
            filename="EA.ex5",
            content_base64=_b64(changed),
            overwrite=True,
        )
        assert replaced["sha256"] == hashlib.sha256(changed).hexdigest()

        listed = await server.ok("list_experts")
        assert [(e["name"], e["sha256"]) for e in listed["experts"]] == [
            ("EA.ex5", replaced["sha256"]),
        ]

        deleted = await server.ok("delete_expert", name="EA.ex5")
        assert deleted == {"deleted": "EA.ex5"}
        assert (await server.ok("list_experts"))["experts"] == []

    _run(scenario())


def test_upload_set_accepts_text_and_mt5_utf16_bytes(server):
    async def scenario():
        from_text = await server.ok("upload_set", filename="gold.set", content=SET_TEXT)
        assert {"name": "Lots", "value": "0.10"} in from_text["inputs"]

        utf16 = SET_TEXT.encode("utf-16")
        from_bytes = await server.ok(
            "upload_set", filename="silver.set", content_base64=_b64(utf16)
        )
        assert {"name": "Magic", "value": "777"} in from_bytes["inputs"]

        parsed = await server.ok("get_set", name="silver.set")
        assert parsed["inputs"] == from_bytes["inputs"]

        listed = await server.ok("list_sets")
        assert sorted(s["name"] for s in listed["sets"]) == ["gold.set", "silver.set"]

        deleted = await server.ok("delete_set", name="gold.set")
        assert deleted == {"deleted": "gold.set"}
        listed = await server.ok("list_sets")
        assert [s["name"] for s in listed["sets"]] == ["silver.set"]

    _run(scenario())


# ── Deployments ───────────────────────────────────────────────────────


def test_deployment_lifecycle_through_the_tools(server, chartctl_app):
    async def scenario():
        await server.ok("upload_expert", filename="EA.ex5", content_base64=_b64(EXPERT_BYTES))
        await server.ok("upload_set", filename="gold.set", content=SET_TEXT)

        created = await server.ok(
            "create_deployment",
            expert="EA.ex5",
            symbol="XAUUSD",
            timeframe="M5",
            set_file="gold.set",
        )
        dep_id = created["id"]
        assert created["status"] == "pending"
        assert created["deployment"]["set_file"] == "gold.set"

        status, body = await server.rejected(
            "create_deployment", expert="EA.ex5", symbol="XAUUSD", timeframe="M5"
        )
        assert (status, body["code"]) == (409, "DUPLICATE_CHART")

        FakeLoader(chartctl_app.proto_dir).reconcile()

        running = await server.ok("get_deployment", deployment_id=dep_id)
        assert running["status"] == "running"
        charts = await server.ok("list_charts")
        assert [c["deployment_id"] for c in charts["charts"]] == [dep_id]
        assert (await server.ok("get_loader"))["alive"] is True
        listed = await server.ok("list_deployments")
        assert [d["id"] for d in listed["deployments"]] == [dep_id]

        paused = await server.ok("update_deployment", deployment_id=dep_id, enabled=False)
        assert paused["deployment"]["enabled"] is False
        cleared = await server.ok("update_deployment", deployment_id=dep_id, set_file="")
        assert cleared["deployment"]["set_file"] is None
        assert cleared["deployment"]["enabled"] is False

        revision = await server.ok("reconcile_deployments")
        assert revision["revision"] >= 1

        deleted = await server.ok("delete_deployment", deployment_id=dep_id)
        assert deleted["deleted"] == dep_id
        status, body = await server.rejected("get_deployment", deployment_id=dep_id)
        assert (status, body["code"]) == (404, "NOT_FOUND")

    _run(scenario())


def test_create_deployment_sends_enabled_false_and_omits_an_empty_set(server):
    async def scenario():
        await server.ok("upload_expert", filename="EA.ex5", content_base64=_b64(EXPERT_BYTES))
        created = await server.ok(
            "create_deployment",
            expert="EA.ex5",
            symbol="EURUSD",
            timeframe="h1",
            enabled=False,
        )
        assert created["deployment"]["enabled"] is False
        assert created["deployment"]["set_file"] is None
        assert created["deployment"]["timeframe"] == "H1"

    _run(scenario())


# ── Charts ────────────────────────────────────────────────────────────


def test_screenshot_chart_returns_png_image_content(server, chartctl_app):
    async def scenario():
        with _loader_answering_commands(chartctl_app.proto_dir):
            content = await server.content("screenshot_chart", chart_id=133039100)
        assert len(content) == 1
        image = content[0]
        assert image.type == "image"
        assert image.mimeType == "image/png"
        assert base64.b64decode(image.data).startswith(PNG_MAGIC)

    _run(scenario())


def test_screenshot_chart_reports_a_loader_timeout_as_an_error(server):
    async def scenario():
        with pytest.raises(ToolError, match="504"):
            await server.content("screenshot_chart", chart_id=1)

    _run(scenario())


def test_close_chart_round_trips_through_the_loader(server, chartctl_app):
    async def scenario():
        with _loader_answering_commands(chartctl_app.proto_dir):
            closed = await server.ok("close_chart", chart_id=5)
            status, body = await server.rejected("close_chart", chart_id=-1)
        assert closed == {"closed": 5}
        assert (status, body["code"]) == (502, "CLOSE_FAILED")

    _run(scenario())


# ── WebRequest allowlist ──────────────────────────────────────────────


@pytest.fixture
def applied(monkeypatch, tmp_path):
    """Point the WebRequest handlers at a tmp config dir and record applies
    instead of driving the terminal's windows or restarting it."""
    from mt5api.handlers import webrequest

    calls: list[tuple[list[str], bool]] = []

    def record_apply(urls, *, partial=False, reason="put"):
        calls.append((list(urls), partial))
        return True, "test"

    cfg_dir = tmp_path / "Config"
    cfg_dir.mkdir()
    monkeypatch.setattr(webrequest, "_cfg_dir", lambda: str(cfg_dir))
    monkeypatch.setattr(webrequest, "_apply", record_apply)
    return calls


def test_webrequest_tools_replace_edit_and_reapply(server, applied):
    async def scenario():
        assert (await server.ok("get_webrequest"))["urls"] == []

        replaced = await server.ok("set_webrequest", urls=["https://a.example"])
        assert replaced["urls"] == ["https://a.example"]
        assert applied[-1] == (["https://a.example"], False)

        status, body = await server.rejected(
            "set_webrequest", urls=["https://a.example", "ftp://refused.example"]
        )
        assert status == 400
        assert body["code"] == "INVALID_URL"
        assert body["rejected"] == ["ftp://refused.example"]

        edited = await server.ok(
            "set_webrequest",
            add=["https://b.example"],
            remove=["https://a.example"],
            runas=True,
        )
        assert edited["urls"] == ["https://b.example"]
        assert applied[-1] == (["https://b.example"], False)
        assert (await server.ok("get_webrequest"))["urls"] == ["https://b.example"]

        reapplied = await server.ok("apply_webrequest", runas=True)
        assert reapplied["urls"] == ["https://b.example"]
        assert applied[-1] == (["https://b.example"], True)  # the re-apply is partial

        cleared = await server.ok("set_webrequest", urls=[])
        assert cleared["urls"] == []

    _run(scenario())


def test_verify_webrequest_is_refused_outside_the_vm(server, applied):
    async def scenario():
        status, body = await server.rejected("verify_webrequest")
        assert status == 409
        assert body["code"] == "UNAVAILABLE"

    _run(scenario())


# ── Arguments refused before any request ──────────────────────────────


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("upload_expert", {"filename": "EA.ex5", "content_base64": ""}, "is empty"),
        (
            "upload_expert",
            {"filename": "EA.ex5", "content_base64": "not base64!"},
            "not valid base64",
        ),
        (
            "upload_set",
            {"filename": "x.set", "content": "A=1", "content_base64": _b64(b"A=1")},
            "exactly one",
        ),
        ("upload_set", {"filename": "x.set"}, "exactly one"),
        ("update_deployment", {"deployment_id": "dep_x"}, "nothing to change"),
        (
            "set_webrequest",
            {"urls": ["https://a.example"], "add": ["https://b.example"]},
            "not both",
        ),
        ("set_webrequest", {}, "pass urls"),
        ("delete_expert", {"name": "../deployments/dep_x"}, "single name"),
        ("delete_expert", {"name": ".."}, "single name"),
        ("get_set", {"name": ""}, "single name"),
        ("delete_set", {"name": "../experts/EA.ex5"}, "single name"),
        ("get_deployment", {"deployment_id": "dep/x"}, "single name"),
        ("update_deployment", {"deployment_id": "a\\b", "enabled": False}, "single name"),
        ("delete_deployment", {"deployment_id": "."}, "single name"),
    ],
)
def test_bad_arguments_are_refused_before_any_request(server, tool, args, message):
    async def scenario():
        with pytest.raises(ToolError, match=message):
            await server.content(tool, **args)
        assert server.requests == 0

    _run(scenario())


def test_path_arguments_are_encoded_not_parsed(server):
    """A ``?`` in an id must stay part of the id. Sent raw, it would start a
    query string and the request would fetch the deployment before it."""
    async def scenario():
        await server.ok("upload_expert", filename="EA.ex5", content_base64=_b64(EXPERT_BYTES))
        created = await server.ok(
            "create_deployment", expert="EA.ex5", symbol="XAUUSD", timeframe="M5"
        )
        status, body = await server.rejected(
            "get_deployment", deployment_id=created["id"] + "?x=1"
        )
        assert (status, body["code"]) == (404, "NOT_FOUND")

    _run(scenario())


def test_wrapped_base64_is_accepted(server):
    """``base64`` wraps its output at 76 columns; agents pass it on as is."""
    payload = bytes(range(256)) * 3
    wrapped = base64.encodebytes(payload).decode("ascii")
    assert "\n" in wrapped

    async def scenario():
        staged = await server.ok("upload_expert", filename="EA.ex5", content_base64=wrapped)
        assert staged["sha256"] == hashlib.sha256(payload).hexdigest()

    _run(scenario())


# ── Unified server only ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        (False, f"Chart Deployments are not enabled on terminal '{TERMINAL_KEY}'"),
        (True, "although config.yaml enables chartctl for it"),
    ],
)
def test_unified_chartctl_tool_on_a_terminal_without_the_routes_says_so(
    configured,
    message,
):
    async def scenario():
        mcp = _unified_server(_FlaskTransport(Flask("no_chartctl")), chartctl=configured)
        with pytest.raises(ToolError) as excinfo:
            await mcp.call_tool("list_charts", {"broker": BROKER, "account": ACCOUNT})
        assert message in str(excinfo.value)
        assert "restart the stack" in str(excinfo.value)

    _run(scenario())


def test_unified_upload_carries_the_bearer_token():
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"name": "EA.ex5"})

    async def scenario():
        mcp = _unified_server(httpx.MockTransport(answer), api_token="s3cret-token")
        await mcp.call_tool(
            "upload_expert",
            {
                "broker": BROKER,
                "account": ACCOUNT,
                "filename": "EA.ex5",
                "content_base64": _b64(EXPERT_BYTES),
            },
        )

    _run(scenario())
    (request,) = seen
    assert request.headers["authorization"] == "Bearer s3cret-token"
    assert request.headers["content-type"].startswith("multipart/form-data")


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("set_webrequest", {"urls": ["https://a.example"]}),
        ("apply_webrequest", {}),
        ("verify_webrequest", {}),
    ],
)
def test_unified_webrequest_calls_outlast_the_default_timeout(tool, args):
    """An apply can wait minutes for a bare-metal terminal restart, longer
    than the 120s default request timeout."""
    from mcpunifier.constants import (
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
        WEBREQUEST_TIMEOUT_SECONDS,
    )

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True, "urls": []})

    async def scenario():
        mcp = _unified_server(httpx.MockTransport(answer))
        await mcp.call_tool(tool, {"broker": BROKER, "account": ACCOUNT, **args})

    _run(scenario())
    (request,) = seen
    assert request.extensions["timeout"]["read"] == WEBREQUEST_TIMEOUT_SECONDS
    assert WEBREQUEST_TIMEOUT_SECONDS > DEFAULT_REQUEST_TIMEOUT_SECONDS


def test_unified_screenshot_refuses_a_non_png_answer():
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "an image"})

    async def scenario():
        mcp = _unified_server(httpx.MockTransport(answer))
        with pytest.raises(ToolError, match="expected 'image/png'"):
            await mcp.call_tool(
                "screenshot_chart",
                {"broker": BROKER, "account": ACCOUNT, "chart_id": 1},
            )

    _run(scenario())


def test_unified_screenshot_sends_the_requested_size():
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=PNG_MAGIC, headers={"content-type": "image/png"})

    async def scenario():
        mcp = _unified_server(httpx.MockTransport(answer))
        await mcp.call_tool(
            "screenshot_chart",
            {"broker": BROKER, "account": ACCOUNT, "chart_id": 9, "width": 640, "height": 360},
        )

    _run(scenario())
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/charts/9/screenshot"
    assert dict(seen[0].url.params) == {"width": "640", "height": "360"}


def test_unified_endpoints_lists_every_chartctl_route_as_requiring_chartctl():
    """The unified catalog is hand-maintained; this pins its chartctl part to
    the route table the terminal actually registers."""
    from mt5api.handlers.chartctl_routes import register_chartctl_routes

    app = Flask("catalog")
    register_chartctl_routes(app)
    registered = {
        (method, re.sub(r"<[a-zA-Z_]+:", "<", rule.rule))
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }

    async def scenario():
        mcp = _unified_server(httpx.MockTransport(lambda request: httpx.Response(500)))
        content = _content_blocks(await mcp.call_tool("endpoints", {}))
        return json.loads(content[0].text)["endpoints"]

    entries = _run(scenario())
    gated = {(e["method"], e["path"]) for e in entries if e.get("requires") == "chartctl"}
    assert gated == registered


def test_unified_screenshot_defaults_match_the_handler():
    from mcpunifier import constants
    from mt5api.chartctl import command

    assert constants.SCREENSHOT_DEFAULT_WIDTH == command.SCREENSHOT_DEFAULT_WIDTH
    assert constants.SCREENSHOT_DEFAULT_HEIGHT == command.SCREENSHOT_DEFAULT_HEIGHT


# ── Tool registration ─────────────────────────────────────────────────


def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in _run(mcp.list_tools())}


def test_terminal_server_registers_chartctl_tools_only_when_enabled():
    from mt5api.mcp_server import build_mcp_server

    without = _tool_names(build_mcp_server(chartctl_enabled=False))
    with_chartctl = _tool_names(build_mcp_server(chartctl_enabled=True))

    assert not without & CHARTCTL_TOOLS
    assert with_chartctl - without == CHARTCTL_TOOLS


def test_unified_server_always_registers_the_chartctl_tools():
    unified = _unified_server(httpx.MockTransport(lambda request: httpx.Response(500)))

    assert _tool_names(unified) >= CHARTCTL_TOOLS
