"""HTTP-level tests for input validation on /symbols/<sym>/rates and
/symbols/<sym>/ticks. Mocks ensure_initialized + symbol_info so the
handlers reach the validation logic without touching MT5.

We don't test the success paths here (those need realistic MT5 fixtures);
we test that bad inputs return 400 with the right error shape.
"""

from unittest.mock import MagicMock

import pytest

import mt5api.handlers.symbols as h
from mt5api.server import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(h, "ensure_initialized", lambda: True)
    # Make _ensure_symbol pass for any symbol
    fake_info = MagicMock(visible=True)
    monkeypatch.setattr(h.mt5, "symbol_info", lambda s: fake_info)
    monkeypatch.setattr(h.mt5, "symbol_select", lambda s, v: True)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("path,desc", [
    ("/symbols/EURUSD/rates?from=1700000000&to=1700001000&count=10",
     "rates: count and to mutually exclusive"),
    ("/symbols/EURUSD/rates?to=1700000000",
     "rates: to without from"),
    ("/symbols/EURUSD/rates?from=garbage&count=5",
     "rates: malformed from"),
    ("/symbols/EURUSD/rates?from=1700000000&to=garbage",
     "rates: malformed to"),
    ("/symbols/EURUSD/rates?timeframe=BOGUS&count=5",
     "rates: invalid timeframe"),
    ("/symbols/EURUSD/ticks?from=1700000000&to=1700001000&count=10",
     "ticks: count and to mutually exclusive"),
    ("/symbols/EURUSD/ticks?to=1700000000",
     "ticks: to without from"),
    ("/symbols/EURUSD/ticks?from=garbage&count=5",
     "ticks: malformed from"),
    ("/symbols/EURUSD/ticks?from=1700000000&to=garbage",
     "ticks: malformed to"),
    ("/symbols/EURUSD/ticks?flags=BOGUS&count=5",
     "ticks: invalid flags"),
])
def test_validation_returns_400(client, path, desc):
    resp = client.get(path)
    assert resp.status_code == 400, f"{desc}: got {resp.status_code} {resp.data}"
    body = resp.get_json()
    assert body is not None and "error" in body, f"{desc}: missing error field"


@pytest.mark.parametrize("path,desc", [
    ("/symbols/EURUSD/rates?count=0", "rates: count=0 returns empty array"),
    ("/symbols/EURUSD/ticks?count=0", "ticks: count=0 returns empty array"),
])
def test_count_zero_short_circuits(client, monkeypatch, path, desc):
    # Should never reach MT5 with count=0
    monkeypatch.setattr(h.mt5, "copy_rates_from_pos", MagicMock(side_effect=AssertionError("must not call")))
    monkeypatch.setattr(h.mt5, "copy_ticks_from", MagicMock(side_effect=AssertionError("must not call")))
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.get_json() == []
