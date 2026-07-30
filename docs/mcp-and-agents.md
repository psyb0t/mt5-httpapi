# MCP and agent integrations

Expose the REST surface as MCP tools and install the repository's reusable agent integrations.

## Contents

- [MCP interface](#mcp-interface)
- [One endpoint for every terminal](#one-endpoint-for-every-terminal)
- [Agent integrations](#agent-integrations)

## MCP Interface

There are two [Model Context Protocol](https://modelcontextprotocol.io) endpoints (both streamable-HTTP), and the URL you point a client at decides which one it gets:

| Point the client at | You get |
|---|---|
| `http://host:8888/<broker>/<account>/mcp/` | that **one** terminal — tools take no account parameter |
| `http://host:8888/mcp/` | **every** terminal — the same tools, plus `broker` / `account` parameters, plus `list_terminals` |

Both are always available; neither disables the other. See [One endpoint for every terminal](#one-endpoint-for-every-terminal) for the unified form.

Every terminal mounts its own server at `/mcp`, alongside the REST API, in the same process. It exposes **dedicated, typed tools** grouped by family — each tool's name, typed params, and description are what the agent reads (no guessing at raw paths). Every tool runs the exact same handler, auth, and MT5 locking as a real HTTP request.

- **Market data** — `list_symbols`, `get_symbol`, `get_tick`,
  `get_rates(symbol, timeframe, count?, from_?, to?)`,
  `get_ticks(symbol, count?, from_?, to?, flags?)`, and
  `get_rates_ta(symbol, timeframe, indicators, count?, from_?, to?)`
- **Account / positions** — `get_account`, `list_positions`, `get_position`, `modify_position`, `close_position`
- **Orders** — `list_orders`, `get_order`, `create_order(symbol, type, volume, price?, sl?, tp?)`, `modify_order`, `cancel_order`
- **History / terminal / backtest** — `get_history_orders`, `get_history_deals`, `get_terminal`, `terminal_control`, `get_backtest`, `ping`
- **Escape hatch** — `request(method, path, query?, body?)` + `endpoints` (route catalog) for JSON-compatible routes without a dedicated tool. Multipart uploads such as `POST /backtest` still use REST directly.

The order/position tools (`create_order`, `cancel_order`, `close_position`, …) are irreversible live-account actions and say so in their tool descriptions.

Same bearer auth as REST: an empty `api_token` disables auth on `/mcp` too; a configured token requires `Authorization: Bearer <token>` on every MCP call.

The reachable URL is the terminal's normal base plus `/mcp/` — nginx strips `/<broker>/<account>/` and proxies the rest straight through, so:

```
$MT5_API_URL/mcp/
# e.g. http://localhost:8888/roboforex/main/mcp/
```

### One endpoint for every terminal

A per-terminal `/mcp` is bound to that one terminal: an MCP session has a fixed
tool catalog, so there is no per-call slot to say which account to act on. To
drive several terminals from one session, point the client at the server root
instead:

```
http://localhost:8888/mcp/
```

That endpoint exposes the same tools, each taking `broker` and `account` (plus
an optional `instance`, defaulting to `default`). Call `list_terminals` first —
it returns every configured terminal and its process mode (`live` or
`backtest`). This mode is not the brokerage account's live/demo classification;
check `GET /account` before trading. A broker/account pair that is not configured
is refused, with the valid list in the error, rather than being routed somewhere
plausible.

```bash
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  "http://localhost:8888/mcp/" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_terminals","arguments":{}}}'
```

Both forms work at once — the per-terminal endpoints are unchanged, and the URL
alone decides which surface a client gets. Terminals are resolved once from
`config/config.yaml`, never re-probed, so a terminal that is down fails only the
calls naming it; every successful response carries the `terminal` that answered.

```bash
# Raw JSON-RPC — call the request tool directly
curl -sS -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  "$MT5_API_URL/mcp/" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"ping","arguments":{}}}'
```

Order/position mutations reached through `request` are the same live, irreversible trading actions as calling those REST routes directly — confirm parameters before invoking them.

For MCP clients that only speak local stdio servers, the [`@psyb0t/mt5-httpapi`](../.agents/plugins/mt5-httpapi) OpenClaw plugin is a thin stdio↔HTTP bridge to this endpoint.

## Agent integrations

The [skill](../.agents/skills/mt5-httpapi) works in any agent that reads `.agents/skills/`, and installs natively in the clients below.

### Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install mt5-httpapi@psyb0t
```

Claude Code prompts for the API URL and, if auth is enabled, the bearer token — the token is stored in your OS keychain.

That URL decides how much you reach. Give it the **server root** (`http://localhost:8888`) and you get every terminal, with `broker`/`account` on each tool and `list_terminals` to discover them. Give it a **terminal path** (`http://localhost:8888/roboforex/procent`) and you get that one terminal, with no account parameter to get wrong.

### Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add mt5-httpapi@psyb0t
```

Installed via the marketplace, the skill invokes as `$mt5-httpapi:mt5-httpapi`. Codex also picks the skill up automatically with no install in any repo containing `.agents/skills/`, where it invokes as plain `$mt5-httpapi`.

### OpenClaw

The skill is published to ClawHub on every release:

```bash
openclaw skills install @psyb0t/mt5-httpapi
```

For MCP clients that speak local stdio, the [`@psyb0t/mt5-httpapi`](../.agents/plugins/mt5-httpapi) plugin bridges to the terminal's `/mcp` endpoint:

```bash
openclaw plugins install clawhub:@psyb0t/mt5-httpapi
```

Then set `MT5_API_URL` (and `MT5_API_TOKEN` if your terminal has auth enabled).
