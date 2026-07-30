"""Keep the per-terminal and unified typed MCP signatures aligned."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKET_DATA_TOOLS = ("get_rates", "get_ticks", "get_rates_ta")
UNIFIED_ROUTING_PARAMETERS = {"broker", "account", "instance"}


def _tool_parameters(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: [argument.arg for argument in node.args.args]
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in MARKET_DATA_TOOLS
    }


def test_market_data_tool_parameters_match_between_mcp_servers():
    terminal = _tool_parameters(REPO_ROOT / "mt5api" / "mcp_server.py")
    unified = _tool_parameters(REPO_ROOT / "mcpunifier" / "mcp_server.py")

    assert set(terminal) == set(MARKET_DATA_TOOLS)
    assert set(unified) == set(MARKET_DATA_TOOLS)
    for tool in MARKET_DATA_TOOLS:
        unified_parameters = [
            parameter
            for parameter in unified[tool]
            if parameter not in UNIFIED_ROUTING_PARAMETERS
        ]
        assert unified_parameters == terminal[tool]


def test_tick_and_ta_tools_expose_explicit_range_parameters():
    terminal = _tool_parameters(REPO_ROOT / "mt5api" / "mcp_server.py")

    assert {"from_", "to"} <= set(terminal["get_ticks"])
    assert {"from_", "to"} <= set(terminal["get_rates_ta"])
