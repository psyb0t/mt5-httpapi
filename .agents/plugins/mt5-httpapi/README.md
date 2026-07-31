# @psyb0t/mt5-httpapi

This tiny bridge lets an OpenClaw/MCP agent talk to your self-hosted
[mt5-httpapi](https://github.com/psyb0t/mt5-httpapi) terminal without pretending
stdio and remote HTTP are the same fucking thing.

mt5-httpapi already serves Streamable HTTP at `/mcp`. This package uses
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) to translate local
stdio traffic, forward it to the running terminal, and add the bearer token
when required. That is the whole fucking plugin.

> mt5-httpapi is **self-hosted** and talks to a **live trading terminal**. This
> plugin does not ship the MT5 bridge — it connects to a terminal that **you**
> run. See the [mt5-httpapi repo](https://github.com/psyb0t/mt5-httpapi) to
> stand one up.

## What the robot gets

Your agent gets **dedicated typed tools**, not one giant mystery function:
market data (`list_symbols`, `get_symbol`, `get_tick`, `get_rates`,
`get_ticks`, `get_rates_ta`), account/positions (`get_account`,
`list_positions`, `get_position`, `modify_position`, `close_position`), orders
(`list_orders`, `get_order`, `create_order`, `modify_order`, `cancel_order`),
history/terminal/backtest (`get_history_orders`, `get_history_deals`,
`get_terminal`, `terminal_control`, `get_backtest`), and `ping`. A generic
`request` + `endpoints` catalog cover JSON-compatible routes without a
dedicated tool; multipart `POST /backtest` submission still uses REST directly.
When `MT5_API_URL` is the server root, `list_terminals` discovers the valid
broker/account/instance combinations and their `live`/`backtest` process mode;
when it is a terminal path, the session is pinned to that terminal.

The order/position tools (`create_order`, `modify_order`, `cancel_order`,
`modify_position`, `close_position`) are real, irreversible actions on a live
trading account — confirm the parameters before calling them.

## Configuration

| Env var | Required | Description |
|---|---|---|
| `MT5_API_URL` | yes | Server root (for unified tools plus `list_terminals`) or one terminal URL (for a pinned session), e.g. `http://localhost:8888` or `http://localhost:8888/<broker>/<account>`. The bridge appends `/mcp/`. |
| `MT5_API_TOKEN` | no | Bearer token — required whenever the terminal's `api_token` is set (same token as the REST API); omit only if the server has auth disabled. |

## Install

Install the fucker from ClawHub:

```bash
openclaw plugins install clawhub:@psyb0t/mt5-httpapi
```

Then set `MT5_API_URL` (and `MT5_API_TOKEN` if your terminal uses auth) in the
plugin's environment.

## Native remote MCP (no install)

If your MCP client already supports remote Streamable HTTP, you do not need
this bridge at all — point the client straight at `$MT5_API_URL/mcp/`
(with an `Authorization: Bearer <token>` header if your terminal requires
one).

## License

MIT. See [LICENSE](LICENSE).
