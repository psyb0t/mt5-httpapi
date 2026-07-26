# @psyb0t/mt5-httpapi

An OpenClaw/MCP plugin that connects your agent to a self-hosted
[mt5-httpapi](https://github.com/psyb0t/mt5-httpapi) MetaTrader 5 bridge over
the [Model Context Protocol](https://modelcontextprotocol.io).

mt5-httpapi already serves a Streamable-HTTP MCP endpoint at `/mcp` on every
terminal, mounted alongside its REST API. This package is a thin stdio↔HTTP
bridge (via [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)) for MCP
clients that speak local stdio servers — it forwards everything to your
running mt5-httpapi terminal and adds a bearer token when the terminal
requires one.

> mt5-httpapi is **self-hosted** and talks to a **live trading terminal**. This
> plugin does not ship the MT5 bridge — it connects to a terminal that **you**
> run. See the [mt5-httpapi repo](https://github.com/psyb0t/mt5-httpapi) to
> stand one up.

## Tools

The mt5-httpapi MCP tools become available to your agent — **dedicated typed
tools** grouped by family (each with typed params + a description the agent
reads): market data (`list_symbols`, `get_symbol`, `get_tick`, `get_rates`,
`get_ticks`, `get_rates_ta`), account/positions (`get_account`,
`list_positions`, `get_position`, `modify_position`, `close_position`), orders
(`list_orders`, `get_order`, `create_order`, `modify_order`, `cancel_order`),
history/terminal/backtest (`get_history_orders`, `get_history_deals`,
`get_terminal`, `terminal_control`, `get_backtest`), and `ping`. A generic
`request` + `endpoints` catalog cover anything without a dedicated tool.

The order/position tools (`create_order`, `modify_order`, `cancel_order`,
`modify_position`, `close_position`) are real, irreversible actions on a live
trading account — confirm the parameters before calling them.

## Configuration

| Env var | Required | Description |
|---|---|---|
| `MT5_API_URL` | yes | Base URL of one mt5-httpapi terminal, e.g. `http://localhost:8888/<broker>/<account>`. The bridge appends `/mcp/`. |
| `MT5_API_TOKEN` | no | Bearer token — required whenever the terminal's `api_token` is set (same token as the REST API); omit only if the server has auth disabled. |

## Install

Install it into your OpenClaw agent from ClawHub:

```bash
openclaw plugins install clawhub:@psyb0t/mt5-httpapi
```

Then set `MT5_API_URL` (and `MT5_API_TOKEN` if your terminal uses auth) in the
plugin's environment.

## Native remote MCP (no install)

If your MCP client already supports **remote** Streamable-HTTP servers, you
don't need this bridge — point the client straight at `$MT5_API_URL/mcp/`
(with an `Authorization: Bearer <token>` header if your terminal requires
one).

## License

MIT. See [LICENSE](LICENSE).
