"""Liveness + terminal metadata smoke tests."""


def test_ping_returns_ok(client):
    body = client.get("/ping")
    # /ping shape: {"status":"ok","mode":"live"} or similar — accept either dict or string.
    assert body, "empty /ping body"


def test_get_terminal_metadata(client):
    info = client.get("/terminal")
    assert isinstance(info, dict)
    # Common MT5 TerminalInfo fields — should be present after init.
    for key in ("name", "company", "connected"):
        assert key in info, f"/terminal missing key: {key}"
    assert info["connected"] is True, "terminal is not connected to broker"


def test_last_error_endpoint(client):
    err = client.get("/error")
    # Returns the last MT5 SDK error tuple/object — should be JSON-serializable.
    assert err is not None
