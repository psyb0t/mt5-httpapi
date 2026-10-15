# Chart Deployments: remote EA deployment over HTTP

Attach Expert Advisors to charts with set files over the HTTP API. No RDP, no
terminal restart, and no human clicking through the Navigator.

## Contents

- [Quick start](#quick-start)
- [`POST /experts` and the Navigator refresh](#post-experts-and-the-navigator-refresh)
- [`POST /deployments`](#post-deployments)
- [`GET /loader`](#get-loader)
- [`GET /charts`](#get-charts)
- [Screenshots](#post-chartschart_idscreenshot) and [closing charts](#post-chartschart_idclose)
- [WebRequest allowlist](#webrequest-allowlist)
- [Chart Control Protocol](chart-control-protocol.md), the file contract the loader speaks


> **Video walkthrough:** *Coming soon. A full walkthrough of staging, deploying, and
> debugging EAs via the chartctl API.*

Attach Expert Advisors to charts with set files over the HTTP API. No RDP,
no terminal restart. Stage an `.ex5` + `.set`, declare a deployment (expert +
set + symbol + timeframe), and a resident loader EA inside the terminal
reconciles the terminal's actual charts to match. The API holds *desired
state*; the loader reports *observed truth* back, so a deployment only reads
`running` once the expert is confirmed live on a chart.

Any client (a dashboard, a script, an AI agent) drives it over plain REST.
Full protocol contract and file formats: [`docs/chart-control-protocol.md`](chart-control-protocol.md).

AI agents get the same thing as typed MCP tools, file uploads and chart screenshots included. See [MCP and agent integrations](mcp-and-agents.md#mcp-interface).

| Method                                           | Endpoint                              | Description                                              |
| ------------------------------------------------ | ------------------------------------- | -------------------------------------------------------- |
| `POST` / `GET` / `DELETE`                        | `/experts` `/experts/<name>`          | Stage, list, remove EA `.ex5` files                      |
| `POST` / `GET` / `DELETE`                        | `/sets` `/sets/<name>`                | Stage, list, inspect (parsed), remove `.set` parameter files |
| `POST` / `GET`                                   | `/deployments`                        | Create or list deployments                               |
| `GET` / `PATCH` / `DELETE`                       | `/deployments/<id>`                   | Inspect, pause/resume/change set, tear down a deployment |
| `POST`                                           | `/deployments/reconcile`              | Force an immediate reconcile cycle (otherwise periodic)  |
| `GET`                                            | `/charts`                             | Live chart/EA inventory from inside the terminal         |
| `GET`                                            | `/loader`                             | Loader EA status (alive, version, chart open count)      |
| `POST`                                           | `/charts/<chart_id>/screenshot`       | Capture a chart PNG from inside the terminal             |
| `POST`                                           | `/charts/<chart_id>/close`            | Close a chart by id (any chart, including leaks)         |

`DELETE /experts/<name>` and `DELETE /sets/<name>` answer 409 `IN_USE` while any deployment, paused ones included, names the file, because the API rebuilds a deployment's chart template from its expert and set on every change. Delete the deployment first. Host-managed files under `assets/` that were never copied into the terminal answer 403 `HOST_ASSET`. Staged sets live in the terminal's `chartctl/sets/`, which the [file API](files.md) only reads, so remove them here.

**Opt-in.** `chartctl.enabled` defaults to `false`; set it to `true` in
`config.yaml` to switch the feature on. Live-mode terminals only, and any single
terminal can stay clear of it with `chartctl: false`.

That default is deliberate. Enabling this writes a `[StartUp]` section into every
live terminal's INI, so the loader EA attaches itself at launch. That is fleet-wide
behaviour that must be asked for, never inherited from an upgrade. With the block
absent nothing changes and the endpoints return 404.

**Once enabled, setup is none.** On boot, provisioning auto-compiles the bundled loader EA
(`assets/experts/MT5ChartLoader.mq5`) in every broker base and wires a
`[StartUp] Expert=` line into each live terminal's `mt5start.ini`, so the
loader attaches itself at launch. The whole path is API/config-driven, no
RDP. Already have a resident utility EA on every terminal? Adopt the protocol
into it with three calls instead of running a second EA. See
`assets/experts/include/ChartControl.mqh`. The standalone loader steps aside
automatically (single-loader mutex via terminal GlobalVariable).

### Quick start

```bash
# 0. Set your base URL + auth token
export MT5_API_URL=http://localhost:8888/yourbroker/yourlogin
export MT5_API_TOKEN=$(grep api_token config/config.yaml | awk -F'"' '{print $2}')

# 1. Stage an expert (.ex5) and a set file (.set)
curl -H "Authorization: Bearer $MT5_API_TOKEN" \
  -F "expert=@HappyGoldScalp.ex5" "$MT5_API_URL/experts"
curl -H "Authorization: Bearer $MT5_API_TOKEN" \
  -F "set=@gold-m5.set" "$MT5_API_URL/sets"   # returns parsed inputs

# 2. Deploy: declare desired state
curl -X POST "$MT5_API_URL/deployments" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"expert":"HappyGoldScalp.ex5","set":"gold-m5.set","symbol":"XAUUSD","timeframe":"M5"}'
# -> {"id":"dep_a1b2c3","status":"pending"}

# 3. Verify: status flips to "running" once the loader confirms attach
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/deployments"

# 4. Change the set file, pause, or tear down. A new set file reaches the
#    expert the next time the loader opens its chart, not while it runs. To
#    apply it now: pause, wait until GET /charts no longer lists the
#    deployment's chart (its status says "paused" right away, before the
#    loader acts), then resume. Resuming is refused with 409 DUPLICATE_CHART
#    while another enabled deployment runs on the same symbol and timeframe.
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"set":"gold-m5-v2.set"}'
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":false}'
curl -X DELETE "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN"
```

### `POST /experts` and the Navigator refresh

MT5 only loads an expert it saw on disk when the terminal started, or that a Navigator refresh has picked up since. A template naming any other `.ex5` opens the chart with no expert on it, and the deployment fails with `EXPERT_NOT_ATTACHED`. So every successful `POST /experts`, including a re-upload of identical bytes, refreshes the terminal's Navigator, the same way a user would right-click "Expert Advisors" and choose Refresh. The API does it with window messages sent straight to the Navigator (`mt5api/chartctl/winnavigator.py`): nothing is typed into whatever window has focus, and it works with no RDP or VNC viewer attached. It then checks that the expert is listed under Expert Advisors > Uploaded. Deploying a host-managed expert for the first time does the same after copying it in.

The response's `navigator_refresh` says how that went:

| Value | Meaning |
| ----- | ------- |
| `ok` | The expert is in the Navigator; deploy away. `already_listed: true` means it was there before the refresh (a re-upload or an overwrite), so the check proved nothing new. |
| `failed` | The expert is not in the Navigator after the refresh, or the refresh could not run; `navigator_error` says why (`expert_not_listed`, `navigator_not_found`, `backtest_running`, ...). Upload the same file again to retry, or restart the terminal. |
| `unavailable` | No GUI automation on this host (a bare-metal terminal). Restart the terminal before deploying a newly uploaded expert. |

The Navigator's "Expert Advisors" node and its Refresh item are found by label. English is built in; a terminal running MT5 in another language needs its labels in `config.yaml`:

```yaml
chartctl:
  navigator_labels:
    expert_advisors: ["<the node's label, as the Navigator shows it>"]
    refresh: ["<the Refresh item's label in its right-click menu>"]
```

### `POST /deployments`

| Field       | Required | Description                                  |
| ----------- | -------- | -------------------------------------------- |
| `expert`    | yes      | `.ex5` filename (previously staged via `POST /experts`) |
| `set`       | yes      | `.set` filename (previously staged via `POST /sets`)    |
| `symbol`    | yes      | e.g. `EURUSD`                                |
| `timeframe` | yes      | `M1` `M5` `M15` `M30` `H1` `H4` `D1` `W1` `MN` |

Example response:

```json
{
  "id": "dep_a1b2c3",
  "expert": "HappyGoldScalp.ex5",
  "set": "gold-m5.set",
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "enabled": true,
  "status": "pending",
  "revision": 1,
  "created_at": "2026-07-27T12:00:00Z",
  "updated_at": "2026-07-27T12:00:00Z"
}
```

Deployment lifecycle: `pending` → `running` (loader confirmed) → `degraded`
(loader sees an error) → `failed` (unrecoverable) or `paused` (disabled by
user).

### `GET /loader`

Returns the resident loader EA's status:

```json
{
  "alive": true,
  "version": "1.0.2",
  "charts_open": 3,
  "observed_revision": 5,
  "desired_revision": 5,
  "in_sync": true
}
```

If `alive` is `false`, the loader hasn't started yet. Check that
`chartctl.enabled` is on and the terminal was restarted after provisioning.
The first boot compile log lives at `logs/compile-chartctl-loader.log` inside
the VM.

### `GET /charts`

Lists every chart known to the terminal, annotated with which deployment (if
any) the loader attributes it to:

```json
{
  "charts": [
    {"id": 123, "symbol": "XAUUSD", "timeframe": "M5",
     "expert": "HappyGoldScalp.ex5", "deployment_id": "dep_a1b2c3"},
    {"id": 456, "symbol": "EURUSD", "timeframe": "H1",
     "expert": "", "deployment_id": null}
  ]
}
```

Charts with no deployment (e.g. leftover duplicates) can be closed with
`POST /charts/<id>/close`.

### `POST /charts/<chart_id>/screenshot`

Captures a PNG of the chart from inside the terminal. Returns the raw binary
(`image/png`). No query-string auth. Put the token in the header.

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/charts/123/screenshot" -o xauusd-m5.png
```

### `POST /charts/<chart_id>/close`

Sends a `close_chart` command to the loader. The loader refuses to close its
own chart (returns `CLOSE_REFUSED`).

```json
// Response
{"command_id":"cmd_xxx","status":"accepted"}
```

Poll the deployment status to confirm the chart was recreated if expected.

## WebRequest allowlist

EAs that call `WebRequest()` need their target hosts in the terminal's
allowlist (Tools → Options → Expert Advisors → *Allow WebRequest for*).
It's a dedicated call rather than something on the deploy hot path (most
deployments need no URLs):

| Method | Endpoint                  | Description                           |
| ------ | ------------------------- | ------------------------------------- |
| `GET`  | `/webrequest`             | Stored allowlist and the last apply result (`last_apply`) |
| `PUT`  | `/webrequest`             | Replace (`urls`) or patch (`add`/`remove`) the allowlist |
| `POST` | `/webrequest/apply`       | Re-apply the stored list now          |
| `POST` | `/webrequest/verify`      | Read the list the terminal really holds (VM only) |

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/webrequest"
curl -X PUT "$MT5_API_URL/webrequest" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"add":["https://api.telegram.org"]}'   # or {"urls":[...]} to replace
# -> {"success":true,"urls":[...],"applied_via":"win32:OK"}
curl -X POST "$MT5_API_URL/webrequest/apply" \
  -H "Authorization: Bearer $MT5_API_TOKEN"   # re-apply current list now
curl -X POST "$MT5_API_URL/webrequest/verify" \
  -H "Authorization: Bearer $MT5_API_TOKEN"
# -> {"urls":[...stored...],"live":{"enabled":true,"urls":[...]},"terminal_build":6257}
```

- `{"urls": [...]}` makes the terminal's list exactly that, removing every other entry, including ones added by hand in the VM.
- `{"add": [...], "remove": [...]}` changes only the URLs named. Every other entry the terminal holds stays, and the list read back becomes the stored list, so URLs added by hand survive restarts too.
- Only `http(s)` URLs with a host, at most 750 characters (MT5 refused a 1120-character one) and no spaces, `;` or control characters are accepted. Anything else answers 400 `INVALID_URL` with the offending entries under `rejected`, and nothing is stored.
- A failed apply answers with a `code`: 409 for a state to wait out (`BACKTEST_RUNNING`, or `TERMINAL_BUSY` when the terminal shows a dialog such as a login), 503 when the terminal is not running (`PID_NOT_FOUND`, `WINDOW_NOT_FOUND`), 500 otherwise. While a backtest runs on the terminal, `PUT` and `verify` answer 409: the tester runs from the same `terminal64.exe`. The background re-apply waits for the run to end.
- `urls`, `add` and `remove` must be lists; anything else is a 400, never an empty list.
- `verify` opens the Options dialog for a moment, so a second call within 10 seconds answers 429 with `Retry-After`. Use it to check an apply, not to poll.

Inside the Windows VM every terminal starts with `/config:mt5start.ini`, and a terminal started that way never writes its settings back, so it forgets the list on every restart (the common.ini codec also cannot decode the lists builds 6238 and later write). So the list is set the way a user would, in Tools > Options > Expert Advisors, but with window messages sent straight to the dialog's controls (`mt5api/chartctl/winoptions.py`): nothing is typed into whatever window has focus, no window has to be in front, and it works with no RDP or VNC viewer attached. It takes effect immediately in-session and takes seconds. OK is pressed only when every change took; otherwise the dialog is cancelled and nothing is saved. Because MT5 forgets the list on restart, the API re-applies the persisted list about 25 s after its own process starts, which covers the periodic auto-reboot, and the same delay after every terminal restart it performs itself (the health monitor's recovery restart, `POST /terminal/restart`). A terminal that starts again without the API restarting it (a LiveUpdate, a terminal the SDK started after a crash, one started by hand) is noticed within about 10 seconds: the API reconnects to it and re-applies the list after the same settle delay. That re-apply, like `POST /webrequest/apply`, skips a stored URL the terminal will not take and reports it under `rejected` instead of leaving the terminal with no list. `GET /webrequest` shows the running terminal (`terminal_pid`, `terminal_started_at`) and `stale: true` while it has not been given the stored list yet; after 5 minutes stale the API log shows `!!! WEBREQUEST LIST NOT RESTORED !!!` on every health pass. `POST /webrequest/apply` is for when that error stays. On a bare-metal terminal where `common.ini` *is* the store, it falls back to writing `common.ini` + restarting.

The desired list is persisted per terminal (`Config/webrequest.json`); the
first call migrates whatever the terminal already has, so manually configured
URLs are preserved. Every apply writes its outcome to `Config/webrequest_status.json`,
which `GET /webrequest` returns as `last_apply` (`at`, `reason`, `status`, `code`,
`rejected`). Since nothing depends on focus, terminals apply their lists in
parallel; each terminal's boot re-apply is still staggered by its port so one
VM's terminals do not all open their Options dialogs in the same second.
