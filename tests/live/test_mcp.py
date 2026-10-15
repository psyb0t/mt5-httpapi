"""Both MCP endpoints against the running stack: tool lists, routing, the
route catalog, and answers that match the REST API."""
from __future__ import annotations

import pytest

from tests.live.live_client import LiveAPIError, unwrap_terminal_result

CORE_TOOLS = frozenset({
    "ping", "get_terminal", "terminal_control", "get_account",
    "list_symbols", "get_symbol", "get_tick", "get_rates", "get_ticks", "get_rates_ta",
    "list_positions", "get_position", "modify_position", "close_position",
    "list_orders", "get_order", "create_order", "modify_order", "cancel_order",
    "get_history_orders", "get_history_deals", "get_backtest",
    "request", "endpoints",
})
CHARTCTL_TOOLS = frozenset({
    "list_experts", "upload_expert", "delete_expert",
    "list_sets", "get_set", "upload_set", "delete_set",
    "list_deployments", "get_deployment", "create_deployment",
    "update_deployment", "delete_deployment", "reconcile_deployments",
    "list_charts", "get_loader", "screenshot_chart", "close_chart",
    "get_webrequest", "set_webrequest", "apply_webrequest", "verify_webrequest",
})
_HTTP_NOT_FOUND = 404


def test_unified_endpoint_lists_every_tool(mcp_unified):
    tools = mcp_unified.tool_names()

    assert tools >= CORE_TOOLS | CHARTCTL_TOOLS | {"list_terminals"}


def test_terminal_endpoint_lists_chartctl_tools_only_when_enabled(mcp_terminal, chartctl_enabled):
    tools = mcp_terminal.tool_names()

    assert tools >= CORE_TOOLS
    if chartctl_enabled:
        assert tools >= CHARTCTL_TOOLS
    else:
        assert not tools & CHARTCTL_TOOLS


def test_list_terminals_reports_the_target_and_its_chartctl_state(
    mcp_unified, target, chartctl_enabled,
):
    terminals = mcp_unified.call_json("list_terminals", routed=False)["terminals"]

    entry = next(t for t in terminals if t["key"] == target.terminal_key)
    assert entry["chartctl"] is chartctl_enabled


def test_every_catalogued_route_exists_on_the_terminal(mcp_unified, rest, target):
    """A catalog entry that answers 404 sends agents at a route that is not
    there. Only parameter-free GET routes are probed, and only those whose
    `requires` feature list_terminals reports on for the target; a 4xx other
    than 404 (a missing query parameter) still proves the route exists."""
    entries = mcp_unified.call_json("endpoints", routed=False)["endpoints"]
    terminals = mcp_unified.call_json("list_terminals", routed=False)["terminals"]
    features = next(t for t in terminals if t["key"] == target.terminal_key)
    probed = []
    for entry in entries:
        if entry["method"] != "GET" or "<" in entry["path"]:
            continue
        if entry.get("requires") and not features.get(entry["requires"]):
            continue
        resp = rest.session.get(rest.base + entry["path"], timeout=60)
        is_json = "json" in resp.headers.get("Content-Type", "")
        assert resp.status_code != _HTTP_NOT_FOUND or is_json, (
            f"catalogued route {entry['path']} answers 404 on the target"
        )
        probed.append(entry["path"])
    assert "/ping" in probed


def test_both_endpoints_report_the_same_account_as_rest(mcp_unified, mcp_terminal, account):
    unified = mcp_unified.call_json("get_account")
    per_terminal = unwrap_terminal_result(mcp_terminal.call_json("get_account"))

    assert unified["login"] == per_terminal["login"] == account["login"]


def test_unified_rates_match_the_requested_count(mcp_unified, settings):
    bars = mcp_unified.call_json("get_rates", symbol=settings["symbol"], timeframe="M5", count=5)

    assert len(bars["result"]) == 5


def test_an_unknown_terminal_is_refused_with_the_valid_list(mcp_unified, target):
    error = mcp_unified.call_error(
        "ping", routed=False, broker="no-such-broker", account=target.account,
    )

    assert "unknown terminal" in error
    assert target.terminal_key in error


def test_chartctl_tool_on_a_terminal_without_it_says_so(mcp_unified, chartctl_enabled):
    if chartctl_enabled:
        pytest.skip("Chart Deployments are enabled on the target terminal")

    with pytest.raises(LiveAPIError, match="Chart Deployments are not enabled"):
        mcp_unified.call_json("get_loader")
