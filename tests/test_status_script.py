"""Behavioral coverage for scripts/status.sh (`make status`).

On a fresh install one slow request used to stop the whole run: the script
ran under `set -e`, curl exited 28 on its timeout, and the rest of the checks
never ran. Its rates check anchored at the 1970 epoch also made the terminal
download decades of history, which held the API for minutes. These tests run
the real script against a local stub API in a temporary copy of the repo
layout and check what it prints and which requests it sends.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "fake-status-token"
PREFIX = "/testbroker/testaccount"
SLOW_PATH = PREFIX + "/symbols/TESTUSD/tick"
SLOW_SECONDS = 3
CURL_TIMEOUT = "1"

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("bash") is None,
    reason="status.sh needs bash and curl",
)

_BAR = {"time": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}


def _bad_query(query: str) -> bool:
    """The rate and tick queries the real API refuses with 400."""
    params = dict(part.partition("=")[::2] for part in query.split("&") if part)
    return (
        ("count" in params and "to" in params)
        or ("to" in params and "from" not in params)
        or params.get("from") == "not_a_date"
        or params.get("timeframe") == "BOGUS"
    )


def _answer(path: str, query: str) -> tuple[int, object]:
    """A plausible response for every route status.sh calls."""
    route = path[len(PREFIX):]
    if route == "/ping":
        return 200, {"status": "ok"}
    if route == "/terminal":
        return 200, {"connected": True}
    if route == "/error":
        return 200, {"code": 1, "message": "Success"}
    if route == "/symbols/TESTUSD/tick":
        return 200, {"bid": 1.0, "ask": 1.0}
    if route.startswith(("/symbols/TESTUSD/rates", "/symbols/TESTUSD/ticks")) and _bad_query(query):
        return 400, {"error": "bad query"}
    if route == "/symbols":
        return 200, ["TESTUSD"]
    if route == "/symbols/TESTUSD":
        return 200, {"name": "TESTUSD"}
    if route.startswith("/symbols/TESTUSD/rates"):
        if "count=0" in query:
            return 200, []
        return 200, [_BAR] * 5
    if route.startswith("/symbols/TESTUSD/ticks"):
        return 200, [{"time": 0, "bid": 1.0}] * 5
    if route.startswith("/symbols/"):
        return 404, {"error": "not found"}
    if route.startswith("/history/") and "from=" not in query:
        return 400, {"error": "from and to are required"}
    if route in ("/positions", "/orders") or route.startswith("/history/"):
        return 200, []
    return 200, {"login": 1, "balance": 0}


class _StubApi(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        _StubApi.requests.append(self.path)
        path, _, query = self.path.partition("?")
        if self.headers.get("Authorization") != f"Bearer {FAKE_TOKEN}":
            self._send(401, {"error": "unauthorized", "code": "UNAUTHORIZED"})
            return
        if path == SLOW_PATH:
            time.sleep(SLOW_SECONDS)
        status, body = _answer(path, query)
        self._send(status, body)

    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # curl gave up on the slow route first

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def stub_api():
    _StubApi.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def repo_copy(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    for name in ("status.sh", "config_helper.py"):
        shutil.copy2(REPO / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / "config" / "config.yaml").write_text(
        f'api_token: "{FAKE_TOKEN}"\n'
        "accounts:\n"
        "  testbroker:\n"
        "    testaccount:\n"
        "      login: 12345\n"
        '      server: "Test-Server"\n'
        '      password: "not-a-real-password"\n'
        "terminals:\n"
        "  - broker: testbroker\n"
        "    account: testaccount\n"
        "    port: 5001\n",
        encoding="utf-8",
    )
    return tmp_path


def _run_status(repo: Path, port: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "scripts" / "status.sh")],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "API_HOST_PORT": str(port),
            "STATUS_CURL_TIMEOUT": CURL_TIMEOUT,
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_timed_out_request_fails_one_check_and_the_rest_still_run(stub_api, repo_copy):
    result = _run_status(repo_copy, stub_api)

    assert "[ERROR]" not in result.stderr, result.stderr
    assert "FAIL  GET /symbols/TESTUSD/tick" in result.stdout, result.stdout
    assert "PASS  GET /history/deals returns 200" in result.stdout, result.stdout
    assert "PASS: " in result.stdout and "SOME TESTS FAILED" in result.stdout
    assert result.returncode == 1


def test_no_check_anchors_rates_at_the_epoch(stub_api, repo_copy):
    _run_status(repo_copy, stub_api)

    rate_requests = [path for path in _StubApi.requests if "/rates" in path]
    assert rate_requests, "status.sh made no rates request"
    assert not [path for path in rate_requests if "from=86400" in path], rate_requests
