"""Contract tests for the read-only routes: /account, /history/*, /terminal, /ping.

No trading here, so the assertions are about shape rather than blast radius:
does an empty result render `[]` and not `null`, does a missing account 404
instead of returning `null` with a 200, and does the broker's UTC offset get
stamped onto the terminal info the way every downstream timestamp assumes.

"""

from collections import namedtuple

import pytest

from mt5api.handlers import account as account_handler
from mt5api.handlers import history as history_handler
from mt5api.handlers import terminal as terminal_handler

AccountInfo = namedtuple("AccountInfo", "login balance equity currency leverage")
HistoryOrder = namedtuple("HistoryOrder", "ticket symbol volume_initial price_open")
TerminalInfo = namedtuple("TerminalInfo", "connected community_account trade_allowed")

RANGE_QUERY = "from=1700000000&to=1700086400"


@pytest.fixture
def account(patch_handler):
    return patch_handler(account_handler)


@pytest.fixture
def history(patch_handler):
    return patch_handler(history_handler)


@pytest.fixture
def terminal(patch_handler):
    return patch_handler(terminal_handler)


def test_ping_needs_no_terminal_at_all(api_client):
    """/ping is what the container healthcheck hits, so it must answer while
    MT5 is down — otherwise a wedged terminal reads as a dead container and
    gets restarted underneath itself.
    """
    resp = api_client.get("/ping")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_account_returns_the_logged_in_account(api_client, account):
    account.set(
        "account_info",
        AccountInfo(login=123, balance=1000.0, equity=1010.5, currency="USD", leverage=100),
    )

    resp = api_client.get("/account")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["login"] == 123
    assert body["equity"] == 1010.5


def test_no_logged_in_account_is_a_404_not_a_null_body(api_client, account):
    """Returning 200 with `null` would make a logged-out terminal look like a
    successful read of an empty account.
    """
    account.set("account_info", None)

    resp = api_client.get("/account")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_account_reports_503_when_mt5_is_down(api_client, patch_handler):
    patch_handler(account_handler, initialized=False)

    resp = api_client.get("/account")

    assert resp.status_code == 503


@pytest.mark.parametrize("path", ["/history/orders", "/history/deals"])
def test_history_requires_both_range_bounds(api_client, history, path):
    history.set("history_orders_get", [])
    history.set("history_deals_get", [])

    assert api_client.get(path).status_code == 400
    assert api_client.get(f"{path}?from=1700000000").status_code == 400
    assert api_client.get(f"{path}?to=1700086400").status_code == 400


@pytest.mark.parametrize("path", ["/history/orders", "/history/deals"])
def test_history_rejects_a_non_numeric_range(api_client, history, path):
    resp = api_client.get(f"{path}?from=yesterday&to=today")

    assert resp.status_code == 400
    assert "error" in resp.get_json()


@pytest.mark.parametrize(
    "path,sdk_fn",
    [
        ("/history/orders", "history_orders_get"),
        ("/history/deals", "history_deals_get"),
    ],
)
def test_history_renders_an_empty_array_when_the_sdk_returns_none(
    api_client, history, path, sdk_fn
):
    history.set(sdk_fn, None)

    resp = api_client.get(f"{path}?{RANGE_QUERY}")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_history_orders_passes_the_converted_range_positionally(api_client, history):
    """The SDK takes datetimes, not the raw unix seconds off the query string —
    the conversion is where a broker-offset bug would land.
    """
    history.set("history_orders_get", [HistoryOrder(1, "EURUSD", 0.1, 1.2)])

    resp = api_client.get(f"/history/orders?{RANGE_QUERY}")

    assert resp.status_code == 200
    call = history.calls_to("history_orders_get")[0]
    assert len(call["args"]) == 2
    date_from, date_to = call["args"]
    assert hasattr(date_from, "year"), "range bound was not converted to a datetime"
    assert date_to > date_from


def test_terminal_info_carries_the_broker_utc_offset(api_client, terminal):
    """Every timestamp the API returns is shifted by this offset, so a client
    that cannot read it cannot interpret any of them.
    """
    terminal.set(
        "terminal_info",
        TerminalInfo(connected=True, community_account=False, trade_allowed=True),
    )

    resp = api_client.get("/terminal")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "broker_utc_offset_hours" in body
    assert "broker_utc_offset_seconds" in body


def test_terminal_reports_503_when_mt5_is_down(api_client, patch_handler):
    patch_handler(terminal_handler, initialized=False)

    resp = api_client.get("/terminal")

    assert resp.status_code == 503
