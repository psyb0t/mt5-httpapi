"""Integration test: the MCP unifier in front of one live and one dead terminal.

Port of the former scripts/test-mcpunifier.sh. Same topology and the same seven
assertions, expressed as pytest against testcontainers so the project has ONE
integration harness in ONE language instead of a bash script alongside it.

The unifier fans one MCP endpoint out to N terminals. The property worth
guarding is that a terminal being down is contained: the healthy terminal still
answers, the endpoint stays up, and the dead one reports as unreachable rather
than taking the process with it.

"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.network import Network

PYTHON_IMAGE = "python:3.12-slim"
TERMINAL_ALIAS = "mt5"
UNIFIER_PORT = 6600

# Only LIVE_TERMINAL_PORT is ever bound. The other terminal is
# configured-but-down on purpose: that is the partial-outage case.
LIVE_TERMINAL_PORT = 6545
DOWN_TERMINAL_PORT = 6542

LIVE_BROKER = "ftmo"
LIVE_ACCOUNT = "tenkchallenge"
DOWN_BROKER = "roboforex"
DOWN_ACCOUNT = "procent"

EXPECTED_TOOL_COUNT = 50
EXPECTED_TERMINAL_COUNT = 2

# What the stub terminal does with the chartctl paths it stands in for: echo
# what an upload put on the wire, answer a screenshot with PNG bytes, and give
# /charts the HTML 404 a terminal without chartctl routes answers.
EXPERT_UPLOAD_BYTES = b"MZ-integration-ex5"
SCREENSHOT_BYTES = b"\x89PNG\r\n\x1a\nintegration"
HTTP_OK = 200
REQUEST_TIMEOUT_SECONDS = 10
STARTUP_TIMEOUT_SECONDS = 60

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIFIER_CONFIG_ENV = "TEST_MCP_CONFIG_B64"
UNIFIER_CONFIG_PATH = "/app/config/config.yaml"

# Stands in for one mt5api process. Binds only the live port, so the second
# configured terminal has nothing listening behind it.
FAKE_TERMINAL_SOURCE = f"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = {LIVE_TERMINAL_PORT}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/charts":
            self._send(404, "text/html; charset=utf-8", b"<h1>Not Found</h1>")
            return
        body = json.dumps({{"ok": True, "port": PORT, "path": self.path}}).encode()
        self._send(200, "application/json", body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        received = self.rfile.read(length)
        if self.path.startswith("/charts/") and "/screenshot" in self.path:
            self._send(200, "image/png", {SCREENSHOT_BYTES!r})
            return
        body = json.dumps({{
            "path": self.path,
            "content_type": self.headers.get("Content-Type", ""),
            "has_filename": b'filename="EA.ex5"' in received,
            "has_field": b'name="expert"' in received,
            "has_bytes": {EXPERT_UPLOAD_BYTES!r} in received,
        }}).encode()
        self._send(200, "application/json", body)

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
"""

UNIFIER_BOOTSTRAP = f"""
import base64
import os
from pathlib import Path

config_path = Path({UNIFIER_CONFIG_PATH!r})
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_bytes(base64.b64decode(os.environ[{UNIFIER_CONFIG_ENV!r}]))
os.execvp("python", ["python", "-m", "mcpunifier"])
"""

CONFIG = {
    "api_token": "",
    "chartctl": {"enabled": True},
    "terminals": [
        {
            "broker": LIVE_BROKER,
            "account": LIVE_ACCOUNT,
            "port": LIVE_TERMINAL_PORT,
            "mode": "live",
        },
        {
            "broker": DOWN_BROKER,
            "account": DOWN_ACCOUNT,
            "port": DOWN_TERMINAL_PORT,
            "mode": "backtest",
        },
    ],
}


def _get_status(url):
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_until_healthy(base_url):
    """Readiness is the health endpoint answering, not a log line — the unifier
    is a real service and this is exactly what a load balancer would probe.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return _get_status(f"{base_url}/health")
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    raise TimeoutError(f"unifier never became healthy within {STARTUP_TIMEOUT_SECONDS}s")


def _mcp(base_url, method, params=None):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/mcp",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode())


def _call_tool(base_url, tool, arguments):
    return _mcp(base_url, "tools/call", {"name": tool, "arguments": arguments})


def _tool_text(result):
    """The text payload of a tools/call result, which is where the unifier puts
    both the proxied response and its own error strings.
    """
    return result["result"]["content"][0]["text"]


@pytest.fixture(scope="module")
def unifier():
    encoded_config = base64.b64encode(yaml.safe_dump(CONFIG).encode("utf-8")).decode(
        "ascii"
    )

    # Built from the repo's own Dockerfile so this exercises the shipped image,
    # not an approximation of it.
    with DockerImage(
        path=str(REPO_ROOT), dockerfile_path="Dockerfile.mcpunifier", clean_up=True
    ) as image:
        with Network() as network:
            terminal = (
                DockerContainer(PYTHON_IMAGE)
                .with_network(network)
                .with_network_aliases(TERMINAL_ALIAS)
                .with_command(["python", "-u", "-c", FAKE_TERMINAL_SOURCE])
            )
            with terminal:
                unifier_container = (
                    DockerContainer(str(image))
                    .with_network(network)
                    .with_exposed_ports(UNIFIER_PORT)
                    .with_env(UNIFIER_CONFIG_ENV, encoded_config)
                    .with_command(["python", "-c", UNIFIER_BOOTSTRAP])
                )
                with unifier_container:
                    host = unifier_container.get_container_host_ip()
                    port = unifier_container.get_exposed_port(UNIFIER_PORT)
                    base_url = f"http://{host}:{port}"
                    _wait_until_healthy(base_url)
                    yield base_url


def test_health_returns_200(unifier):
    assert _get_status(f"{unifier}/health") == HTTP_OK


def test_every_tool_is_exposed(unifier):
    """A drop in the tool count means the unifier stopped advertising part of
    the API — invisible to a health check, breaking to every MCP client.
    """
    tools = _mcp(unifier, "tools/list")["result"]["tools"]

    assert len(tools) == EXPECTED_TOOL_COUNT


def test_market_data_tool_schemas_expose_range_queries(unifier):
    tools = {
        tool["name"]: tool["inputSchema"]["properties"]
        for tool in _mcp(unifier, "tools/list")["result"]["tools"]
    }

    assert {"from_", "to"} <= set(tools["get_ticks"])
    assert {"from_", "to"} <= set(tools["get_rates_ta"])


def test_list_terminals_reports_both_configured_terminals(unifier):
    """Both, not just the reachable one: the catalogue reflects configuration,
    so a dead terminal still has to appear.
    """
    payload = json.loads(_tool_text(_call_tool(unifier, "list_terminals", {})))

    assert len(payload["terminals"]) == EXPECTED_TERMINAL_COUNT
    assert {terminal["mode"] for terminal in payload["terminals"]} == {
        "live",
        "backtest",
    }


def test_list_terminals_reports_chartctl_only_on_the_live_terminal(unifier):
    """chartctl is enabled globally in CONFIG, and a backtest-mode process never
    runs it, so only the live terminal reports it."""
    payload = json.loads(_tool_text(_call_tool(unifier, "list_terminals", {})))

    flags = {terminal["broker"]: terminal["chartctl"] for terminal in payload["terminals"]}
    assert flags == {LIVE_BROKER: True, DOWN_BROKER: False}


def test_upload_expert_sends_a_multipart_form_over_http(unifier):
    """The upload crosses a real socket from the shipped image: the terminal
    must see the form field, the filename and the exact decoded bytes."""
    text = _tool_text(
        _call_tool(
            unifier,
            "upload_expert",
            {
                "broker": LIVE_BROKER,
                "account": LIVE_ACCOUNT,
                "filename": "EA.ex5",
                "content_base64": base64.b64encode(EXPERT_UPLOAD_BYTES).decode(),
            },
        )
    )
    received = json.loads(text)

    assert received["path"] == "/experts"
    assert received["content_type"].startswith("multipart/form-data; boundary=")
    assert received["has_field"] is True
    assert received["has_filename"] is True
    assert received["has_bytes"] is True


def test_screenshot_chart_returns_image_content_over_http(unifier):
    result = _call_tool(
        unifier,
        "screenshot_chart",
        {"broker": LIVE_BROKER, "account": LIVE_ACCOUNT, "chart_id": 7},
    )
    (content,) = result["result"]["content"]

    assert content["type"] == "image"
    assert content["mimeType"] == "image/png"
    assert base64.b64decode(content["data"]) == SCREENSHOT_BYTES


def test_a_terminal_without_chartctl_routes_is_reported_as_such(unifier):
    """CONFIG enables chartctl for the live terminal, but the stub serves no
    /charts route: the state of a terminal whose API started before chartctl
    was turned on."""
    result = _call_tool(
        unifier,
        "list_charts",
        {"broker": LIVE_BROKER, "account": LIVE_ACCOUNT},
    )

    assert result["result"]["isError"] is True
    text = _tool_text(result)
    assert "does not serve the Chart Deployments routes" in text
    assert "restart the stack" in text


def test_the_live_terminal_routes_to_its_own_port(unifier):
    text = _tool_text(
        _call_tool(unifier, "ping", {"broker": LIVE_BROKER, "account": LIVE_ACCOUNT})
    )

    assert f"{LIVE_BROKER}/{LIVE_ACCOUNT}" in text
    assert str(LIVE_TERMINAL_PORT) in text


def test_a_down_terminal_fails_alone(unifier):
    """The isolation property. An unreachable terminal reports as unreachable
    instead of taking the endpoint down with it.
    """
    result = _call_tool(unifier, "ping", {"broker": DOWN_BROKER, "account": DOWN_ACCOUNT})

    assert "unreachable" in json.dumps(result)


def test_a_mismatched_broker_account_pair_is_refused(unifier):
    """Both halves exist, but not together. Routing on either alone would hit
    the wrong account — a live one, in this pairing.
    """
    result = _call_tool(
        unifier, "get_account", {"broker": LIVE_BROKER, "account": DOWN_ACCOUNT}
    )

    assert "unknown terminal" in json.dumps(result)


# ── `endpoints` vs the real Flask router ─────────────────────────────
#
# `endpoints` is the only route discovery an agent on the unified endpoint
# gets: it is the catalog the `request` escape hatch is driven from, so a route
# the Flask app serves but the catalog omits is invisible to every such agent.
# POST /symbols/import was exactly that.
#
# The unifier runs out-of-process and cannot read Flask's url_map, so its
# catalog is hand-maintained — which is why it needs a test. This one is
# black-box on the MCP side: it boots the shipped image, speaks the protocol,
# and reads what the public tool returns, so it also fails if tool registration,
# the transport, or response serialization breaks. (The version it replaced
# AST-parsed mcpunifier/mcp_server.py for the `_ROUTE_CATALOG` literal, which
# stayed green through all three of those.)

# Flask reports these on every rule; they are not part of the callable surface.
_NON_API_METHODS = frozenset({"HEAD", "OPTIONS"})
# Werkzeug spells a placeholder with its converter, `<int:ticket>`; the catalog
# spells it `<ticket>`. Normalize the Flask side to the catalog's form rather
# than the reverse: what `endpoints` describes is a path an agent substitutes a
# value into, and the converter is a routing-layer detail with no presence on
# the wire. Stripping it keeps the parameter NAME, which agents do read, so
# renaming `<symbol>` to `<x>` still fails this test.
_CONVERTER_PREFIX = re.compile(r"<[a-zA-Z_][a-zA-Z0-9_]*:")


def _url_map_routes(app):
    routes = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = _CONVERTER_PREFIX.sub("<", str(rule.rule))
        for method in rule.methods - _NON_API_METHODS:
            routes.add((method, path))
    return routes


def _chartctl_routes():
    """(method, path) for the chartctl and WebRequest routes, from the same
    register_chartctl_routes server.py calls when chartctl is enabled."""
    from flask import Flask

    from mt5api.handlers.chartctl_routes import register_chartctl_routes

    app = Flask("chartctl_routes")
    register_chartctl_routes(app)
    return _url_map_routes(app)


def _files_routes():
    """(method, path) for the file API routes, from the same
    register_files_routes server.py calls when the file API is enabled."""
    from flask import Flask

    from mt5api.handlers.files import register_files_routes

    app = Flask("files_routes")
    register_files_routes(app)
    return _url_map_routes(app)


def _flask_routes():
    """(method, path) for every route the real mt5api Flask app serves with
    chartctl and the file API enabled, the configuration the catalog
    documents.

    Imported here rather than at module scope so the MT5 stub tests/conftest.py
    installs is in place first — the SDK wheel is Windows-only.

    Note what is deliberately NOT in url_map and so not expected in the
    catalog: mt5api/main.py mounts the per-terminal MCP app at /mcp through
    werkzeug's DispatcherMiddleware, outside Flask's router entirely. The
    catalog lists the REST surface `request` can call, which is the same set.
    """
    from mt5api.server import app

    return _url_map_routes(app) | _chartctl_routes() | _files_routes()


def test_the_endpoints_tool_returns_exactly_the_real_flask_routes(unifier):
    """Call the public `endpoints` tool over MCP and compare what comes back to
    the Flask router every terminal actually serves.

    Equality, not containment, in both directions: a missing entry hides a real
    route from agents, and a surplus entry sends them at a 404. The chartctl
    routes are registered behind a config flag, so the expected side is the
    app with chartctl enabled (see _flask_routes) rather than a subset check,
    which would pass the empty catalog.
    """
    payload = json.loads(_tool_text(_call_tool(unifier, "endpoints", {})))

    reported = {
        (entry["method"], entry["path"]) for entry in payload["endpoints"]
    }
    expected = _flask_routes()

    assert reported == expected, (
        "`endpoints` and the Flask router disagree.\n"
        f"  only in the Flask app (invisible to agents): {sorted(expected - reported)}\n"
        f"  only in the catalog (agents would 404):      {sorted(reported - expected)}"
    )


def test_the_endpoints_tool_lists_the_backtest_cache_priming_route(unifier):
    """The specific regression the catalog missed, pinned by name.

    GET /symbols answers 409 on a mode: backtest terminal and tells the caller
    to use POST /symbols/import instead. An agent that can only reach the API
    through `request` has no way to find that route if `endpoints` omits it, so
    the advice in the 409 is unfollowable.
    """
    payload = json.loads(_tool_text(_call_tool(unifier, "endpoints", {})))

    assert {"method": "POST", "path": "/symbols/import"} in payload["endpoints"]


def test_the_endpoints_tool_marks_exactly_the_chartctl_routes(unifier):
    """A chartctl route answers 404 on a terminal without chartctl, so the
    catalog has to say which routes need it, and only those."""
    payload = json.loads(_tool_text(_call_tool(unifier, "endpoints", {})))

    marked = {
        (entry["method"], entry["path"])
        for entry in payload["endpoints"]
        if entry.get("requires") == "chartctl"
    }
    assert marked == _chartctl_routes()


def test_the_endpoints_tool_marks_exactly_the_file_routes(unifier):
    """Likewise for the file API: its routes 404 unless files.enabled."""
    payload = json.loads(_tool_text(_call_tool(unifier, "endpoints", {})))

    marked = {
        (entry["method"], entry["path"])
        for entry in payload["endpoints"]
        if entry.get("requires") == "files"
    }
    assert marked == _files_routes()


def test_the_endpoint_is_still_healthy_after_those_failures(unifier):
    """Ordered last on purpose: it asserts the failures above left the process
    serving rather than wedged.
    """
    assert _get_status(f"{unifier}/health") == HTTP_OK
