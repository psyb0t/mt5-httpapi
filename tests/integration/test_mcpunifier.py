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

EXPECTED_TOOL_COUNT = 25
EXPECTED_TERMINAL_COUNT = 2
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
    def do_GET(self):
        body = json.dumps({{"ok": True, "port": PORT, "path": self.path}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    "terminals": [
        {
            "broker": LIVE_BROKER,
            "account": LIVE_ACCOUNT,
            "port": LIVE_TERMINAL_PORT,
            "mode": "demo",
        },
        {
            "broker": DOWN_BROKER,
            "account": DOWN_ACCOUNT,
            "port": DOWN_TERMINAL_PORT,
            "mode": "live",
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


def test_list_terminals_reports_both_configured_terminals(unifier):
    """Both, not just the reachable one: the catalogue reflects configuration,
    so a dead terminal still has to appear.
    """
    payload = json.loads(_tool_text(_call_tool(unifier, "list_terminals", {})))

    assert len(payload["terminals"]) == EXPECTED_TERMINAL_COUNT


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


def test_the_endpoint_is_still_healthy_after_those_failures(unifier):
    """Ordered last on purpose: it asserts the failures above left the process
    serving rather than wedged.
    """
    assert _get_status(f"{unifier}/health") == HTTP_OK
