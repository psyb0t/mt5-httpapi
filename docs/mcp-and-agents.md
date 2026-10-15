# MCP and agent integrations — let the robots trade carefully

The REST API also speaks MCP, so Claude Code, Codex, OpenClaw, or any other compatible robot can use typed tools instead of hallucinating curl commands.

## Contents

- [MCP interface](#mcp-interface)
- [One endpoint for every terminal](#one-endpoint-for-every-terminal)
- [Agent integrations](#agent-integrations)

## MCP Interface

There are two streamable-HTTP [Model Context Protocol](https://modelcontextprotocol.io) endpoints. The URL decides whether your robot gets one terminal or the entire fucking fleet:

| Point the client at | You get |
|---|---|
| `http://host:8888/<broker>/<account>/mcp/` | that **one** terminal — tools take no account parameter |
| `http://host:8888/mcp/` | **every** terminal — the same tools, plus `broker` / `account` parameters, plus `list_terminals` |

Both are always available; neither disables the other. See [One endpoint for every terminal](#one-endpoint-for-every-terminal) for the unified form.

Every terminal serves `/mcp` beside its REST API. The tools are dedicated and typed, so the agent gets real names, parameters, and descriptions instead of guessing raw paths like an idiot. They still hit the same handlers, auth, and MT5 lock as ordinary HTTP calls.

- **Market data** — `list_symbols`, `get_symbol`, `get_tick`,
  `get_rates(symbol, timeframe, count?, from_?, to?)`,
  `get_ticks(symbol, count?, from_?, to?, flags?)`, and
  `get_rates_ta(symbol, timeframe, indicators, count?, from_?, to?)`
- **Account / positions** — `get_account`, `list_positions`, `get_position`, `modify_position`, `close_position`
- **Orders** — `list_orders`, `get_order`, `create_order(symbol, type, volume, price?, sl?, tp?)`, `modify_order`, `cancel_order`
- **History / terminal / backtest** — `get_history_orders`, `get_history_deals`, `get_terminal`, `terminal_control`, `get_backtest`, `ping`
- **Chart Deployments**, only on terminals with [chartctl](chart-deployments.md) enabled:
  - artifacts: `upload_expert(filename, content_base64, overwrite?)`, `list_experts`, `delete_expert`, `upload_set(filename, content? | content_base64?)`, `list_sets`, `get_set`, `delete_set`
  - deployments: `create_deployment(expert, symbol, timeframe, set_file?, enabled?)`, `list_deployments`, `get_deployment`, `update_deployment(deployment_id, enabled?, set_file?)`, `delete_deployment`, `reconcile_deployments`
  - charts: `list_charts`, `get_loader`, `screenshot_chart(chart_id, width?, height?)`, `close_chart`
  - WebRequest allowlist: `get_webrequest`, `set_webrequest(urls? | add?, remove?, runas?)`, `apply_webrequest(runas?)`, `verify_webrequest` (`runas` is ignored and kept for older callers)
- **Files**, only on terminals with the [file API](files.md) enabled: `list_files(path?, tree?)`, `get_file(path, tree?)`, `put_file(path, content? | content_base64?, extract?, tree?)`, `delete_file(path, recursive?, tree?)`. `tree` is `terminal` (the install directory) or `compile` (the MQL5 tree `/compile` builds against).
- **Escape hatch** — `request(method, path, query?, body?)` + `endpoints` (route catalog) for JSON-compatible routes without a dedicated tool. Multipart uploads such as `POST /backtest` still use REST directly.

`request` can't send a multipart upload, which is why chart files get their own tools. `upload_expert` and `upload_set` take the file as base64 and send the same multipart form the REST API wants, so an agent can stage an `.ex5` without curl. `upload_set` also takes plain text for a hand-written set file. `screenshot_chart` returns the PNG as MCP image content, so the agent actually sees the chart.

The order/position tools (`create_order`, `cancel_order`, `close_position`, …) are irreversible live-account actions and say so in their tool descriptions. So are `create_deployment`, `update_deployment`, `delete_deployment` and `close_chart`, because a running deployment is a live EA that can trade. `set_webrequest` and `apply_webrequest` restart the terminal when it runs on bare metal instead of in the Windows VM.

On a per-terminal endpoint the Chart Deployments tools only show up when chartctl is on for that terminal, and the file tools only when the file API is. The unified endpoint always lists both. There, `list_terminals` reports `chartctl` and `files` per terminal, and calling one of those tools on a terminal without the feature tells you to enable it instead of handing back a bare 404.

Same bearer auth as REST: an empty `api_token` disables auth on `/mcp` too; a configured token requires `Authorization: Bearer <token>` on every MCP call.

The reachable URL is the terminal's normal base plus `/mcp/` — nginx strips `/<broker>/<account>/` and proxies the rest straight through, so:

```
$MT5_API_URL/mcp/
# e.g. http://localhost:8888/roboforex/main/mcp/
```

### One endpoint for every terminal

A per-terminal `/mcp` is married to that one terminal: an MCP session has a fixed
tool catalog, so there is no per-call slot to say which account to act on. To
drive several terminals from one session, point the client at the server root
instead:

```
http://localhost:8888/mcp/
```

That endpoint exposes the same tools, each taking `broker` and `account` (plus
an optional `instance`, defaulting to `default`). Call `list_terminals` first.
It returns every configured terminal, its process mode (`live` or
`backtest`), and whether Chart Deployments (`chartctl`) and the file API (`files`) are enabled for it.
This mode is not the brokerage account's live/demo classification;
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

The [skill](../.agents/skills/mt5-httpapi) teaches compatible agents (such as [peen](https://github.com/psyb0t/peen)) how to use the whole mess without YOLOing trades. Install it through whichever robot cage you use:

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
