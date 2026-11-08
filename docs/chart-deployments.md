---
title: "Chart Deployments"
description: "Upload .ex5 and .set files and attach Expert Advisors to live charts over HTTP, plus the WebRequest allowlist the API sets in each terminal."
nav_order: 6
has_children: true
---

# Chart Deployments: remote EA deployment over HTTP

Attach Expert Advisors to charts with set files over the HTTP API. No RDP, no
terminal restart, and no human clicking through the Navigator.

## Contents

- [Quick start](#quick-start)
- [`POST /experts` and the Navigator refresh](#post-experts-and-the-navigator-refresh)
- [`POST /deployments`](#post-deployments)
- [`GET /deployments` and the other deployment routes](#get-deployments-and-the-other-deployment-routes)
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
([`assets/experts/MT5ChartLoader.mq5`](https://github.com/psyb0t/mt5-httpapi/blob/master/assets/experts/MT5ChartLoader.mq5)) in every broker base and wires a
`[StartUp] Expert=` line into each live terminal's `mt5start.ini`, so the
loader attaches itself at launch. The whole path is API/config-driven, no
RDP. Already have a resident utility EA on every terminal? Adopt the protocol
into it with three calls instead of running a second EA. See
[`assets/experts/include/ChartControl.mqh`](https://github.com/psyb0t/mt5-httpapi/blob/master/assets/experts/include/ChartControl.mqh). The standalone loader steps aside
automatically (single-loader mutex via terminal GlobalVariable).

**Settings.** The rest of the `chartctl` block in `config.yaml` tunes the feature. Durations take the same grammar as `utc_offset`, so a bare number is hours (`command_timeout: 30` is 30 hours); write units such as `"30s"`. A duration that cannot be parsed stops the API from starting. A value below the floor is raised to it.

- `reconcile_hint_interval` (default `"5s"`, floor 1 second): written into `desired.json` as `reconcile_interval`, a hint for custom loaders. The bundled loader ignores it and reconciles every second.
- `observed_stale_after` (default `"60s"`, floor 1 second): when the loader's `observed.json` is older than this, the loader counts as not alive in `GET /loader` and `GET /charts`, a running deployment reads `degraded`, and `converged` is false.
- `command_timeout` (default `"30s"`, floor 1 second): how long the API waits for the loader to answer a one-shot command such as a screenshot.
- `max_upload_bytes` (default `16777216`, 16 MiB, floor 1 KiB): the largest `.ex5` or `.set` that `POST /experts` and `POST /sets` take.
- `navigator_labels`: the Navigator's labels on a terminal that does not run MT5 in English; see [`POST /experts` and the Navigator refresh](#post-experts-and-the-navigator-refresh).

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
# -> 202 {"id":"dep_a1b2c3d4","status":"pending","deployment":{...}}

# 3. Verify: status flips to "running" once the loader confirms attach
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/deployments"

# 4. Change the set file, pause, or tear down. A new set file reaches the
#    expert the next time the loader opens its chart, not while it runs. To
#    apply it now: pause, wait until GET /charts no longer lists the
#    deployment's chart (its status says "paused" right away, before the
#    loader acts), then resume. Resuming is refused with 409 DUPLICATE_CHART
#    while another enabled deployment runs on the same symbol and timeframe.
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3d4" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"set":"gold-m5-v2.set"}'
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3d4" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":false}'
curl -X DELETE "$MT5_API_URL/deployments/dep_a1b2c3d4" \
  -H "Authorization: Bearer $MT5_API_TOKEN"
```

### `POST /experts` and the Navigator refresh

MT5 only loads an expert it saw on disk when the terminal started, or that a Navigator refresh has picked up since. A template naming any other `.ex5` opens the chart with no expert on it, and the deployment fails with `EXPERT_NOT_ATTACHED`. So every successful `POST /experts`, including a re-upload of identical bytes, refreshes the terminal's Navigator, the same way a user would right-click "Expert Advisors" and choose Refresh. The API does it with window messages sent straight to the Navigator ([`mt5api/chartctl/winnavigator.py`](https://github.com/psyb0t/mt5-httpapi/blob/master/mt5api/chartctl/winnavigator.py)): nothing is typed into whatever window has focus, and it works with no RDP or VNC viewer attached. It then checks that the expert is listed under Expert Advisors > Uploaded. Deploying a host-managed expert for the first time does the same after copying it in.

`POST /experts` takes the multipart field `expert` and answers 201 with `name`, `sha256` and `size`. Uploading identical bytes under an existing name answers 200 with `skipped: true`; different bytes under an existing name answer 409 `EXISTS` unless the request carries `?overwrite=true`. `POST /sets` takes the field `set`, answers 201 with `name`, `sha256` and the parsed `inputs`, and always replaces a set of the same name, including one a deployment uses. An upload with no file, an empty file, a bad name or a file over `max_upload_bytes` answers 400 `BAD_REQUEST`. `GET /experts` and `GET /sets` list `name`, `size`, `sha256`, `modified_at` and `source` (`uploaded`, or `host` for files under `assets/`).

The response's `navigator_refresh` says how that went:

| Value | Meaning |
| ----- | ------- |
| `ok` | The expert is in the Navigator; deploy away. `already_listed: true` means it was there before the refresh (a re-upload or an overwrite), so the check proved nothing new. |
| `failed` | The expert is not in the Navigator after the refresh, or the refresh could not run; `navigator_error` says why (`expert_not_listed`, `navigator_not_found`, `backtest_running`, `internal_error`, ...). Upload the same file again to retry, or restart the terminal. |
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
| `set`       | no       | `.set` filename (previously staged via `POST /sets`); without one the expert runs on its default inputs |
| `symbol`    | yes      | e.g. `EURUSD`                                |
| `timeframe` | yes      | `M1` `M2` `M3` `M4` `M5` `M6` `M10` `M12` `M15` `M20` `M30` `H1` `H2` `H3` `H4` `H6` `H8` `H12` `D1` `W1` `MN1` |
| `enabled`   | no       | `false` declares the deployment paused; default `true` |

Example response (202):

```json
{
  "id": "dep_a1b2c3d4",
  "status": "pending",
  "deployment": {
    "id": "dep_a1b2c3d4",
    "expert_file": "HappyGoldScalp.ex5",
    "expert_name": "HappyGoldScalp",
    "set_file": "gold-m5.set",
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "enabled": true,
    "created_at": "2026-07-27T12:00:00Z",
    "updated_at": "2026-07-27T12:00:00Z"
  }
}
```

Deploying a host-managed expert from `assets/` copies it into the terminal
first, and the response then also carries the `navigator_refresh` fields.

| Status | `code` | Meaning |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | A missing or bad field, or an unknown timeframe |
| 404 | `ARTIFACT_NOT_FOUND` | The expert or set is not staged |
| 409 | `DUPLICATE_CHART` | Another enabled deployment already runs on this symbol and timeframe |
| 500 | `TPL_GENERATION_FAILED` | The chart template could not be built; nothing is kept |

Deployment status, derived on every read:

| Status | Meaning |
| --- | --- |
| `pending` | Declared; the loader has not confirmed it yet |
| `running` | The loader reports the expert live on a chart |
| `degraded` | The loader last reported it running, but `observed.json` is older than `observed_stale_after` |
| `failed` | The loader reported an error for it (`error` carries `code` and `detail`, e.g. `EXPERT_NOT_ATTACHED`); the loader retries every 60 seconds |
| `paused` | `enabled` is `false` |

### `GET /deployments` and the other deployment routes

`GET /deployments` merges the registry with the loader's report:

```json
{
  "revision": 5,
  "applied_revision": 5,
  "converged": true,
  "observed_stale": false,
  "loader": {"name": "MT5ChartLoader", "version": "1.0.5",
             "last_loop": "2026-07-27 12:00:03", "applied_revision": 5},
  "deployments": [
    {"id": "dep_a1b2c3d4", "status": "running",
     "desired": {"id": "dep_a1b2c3d4", "expert_file": "HappyGoldScalp.ex5",
                 "expert_name": "HappyGoldScalp", "set_file": "gold-m5.set",
                 "symbol": "XAUUSD", "timeframe": "M5", "enabled": true,
                 "created_at": "2026-07-27T12:00:00Z",
                 "updated_at": "2026-07-27T12:00:00Z"},
     "observed": {"id": "dep_a1b2c3d4", "status": "running", "chart_id": 133039117},
     "error": null}
  ]
}
```

`converged` is true when every deployment is `running` or `paused`, the loader
has applied the current revision, and its report is fresh.

- `GET /deployments/<id>` answers one item from that list plus `revision` and
  `observed_stale`, or 404 `NOT_FOUND`.
- `PATCH /deployments/<id>` takes `set` and/or `enabled`. `"set": null` or
  `""` removes the set. It answers `{"id", "deployment"}`. An empty body
  answers 400, an unknown id 404 `NOT_FOUND`, a set that is not staged 404
  `ARTIFACT_NOT_FOUND`, and resuming onto a busy symbol and timeframe 409
  `DUPLICATE_CHART`.
- `DELETE /deployments/<id>` answers `{"deleted": "<id>", "was": {...}}`.
- `POST /deployments/reconcile` rewrites `desired.json` under a new revision
  and answers 202 `{"revision": N}`. The bundled loader reconciles every
  second anyway, so this matters mostly for custom loaders.

### `GET /loader`

Returns the resident loader EA's status:

```json
{
  "alive": true,
  "observed_stale": false,
  "loader": {"name": "MT5ChartLoader", "version": "1.0.5",
             "last_loop": "2026-07-27 12:00:03", "applied_revision": 5},
  "desired_revision": 5,
  "applied_revision": 5
}
```

`alive` is true when `observed.json` exists, names a loader, and is younger
than `observed_stale_after`. When it is false the response adds a `hint`.

If `alive` is `false`, the loader hasn't started yet. Check that
`chartctl.enabled` is on and the terminal was restarted after provisioning.
The first boot compile log lives at `logs/compile-chartctl-loader.log` inside
the VM.

### `GET /charts`

Lists every chart known to the terminal, annotated with which deployment (if
any) the loader attributes it to:

```json
{
  "loader_alive": true,
  "observed_stale": false,
  "loader": {"name": "MT5ChartLoader", "version": "1.0.5",
             "last_loop": "2026-07-27 12:00:03", "applied_revision": 5},
  "auto_trading": true,
  "charts": [
    {"chart_id": 133039117, "symbol": "XAUUSD", "timeframe": "PERIOD_M5",
     "expert": "HappyGoldScalp", "expert_enabled": true,
     "deployment_id": "dep_a1b2c3d4"},
    {"chart_id": 133039118, "symbol": "EURUSD", "timeframe": "PERIOD_H1",
     "expert": "", "expert_enabled": false, "deployment_id": ""}
  ]
}
```

`expert` is the expert's name without `.ex5`, and `deployment_id` is `""` for
a chart no deployment owns.

Charts with no deployment (e.g. leftover duplicates) can be closed with
`POST /charts/<id>/close`. The standalone loader also cleans up after itself:
30 seconds after it starts it closes the bare charts earlier launches left on
its own symbol and timeframe (no expert, no indicators, no drawn objects, no
owning deployment), so they never pile up to MT5's chart limit.

### `POST /charts/<chart_id>/screenshot`

Captures a PNG of the chart from inside the terminal. Returns the raw binary
(`image/png`). No query-string auth. Put the token in the header.

```bash
curl -X POST -H "Authorization: Bearer $MT5_API_TOKEN" \
  "$MT5_API_URL/charts/133039117/screenshot?width=1280&height=720" -o xauusd-m5.png
```

`width` and `height` default to 1280 and 720.

### `POST /charts/<chart_id>/close`

Sends a `close_chart` command to the loader and waits for its answer. On
success it answers `{"closed": 133039117}`.

Poll the deployment status to confirm the chart was recreated if expected.

Both chart commands go through the loader's one-command channel and share
these errors:

| Status | `code` | Meaning |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | `chart_id`, `width` or `height` is not an integer |
| 409 | `LOADER_BUSY` | Another command is in flight; retry |
| 504 | `LOADER_TIMEOUT` | The loader did not answer within `command_timeout` |
| 502 | `CLOSE_REFUSED` | Close: the chart is the loader's own |
| 502 | `CLOSE_FAILED`, `SCREENSHOT_FAILED` | MT5 refused; `error` carries the MT5 error number |
| 502 | `LOADER_ERROR` | Any other loader failure |

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
- Only `http(s)` URLs with a host, at most 750 characters (MT5 build 6230 refused a 1120-character one) and no spaces, `;` or control characters are accepted. Anything else answers 400 `INVALID_URL` with the offending entries under `rejected`, and nothing is stored.
- A body that is not a JSON object, or that has none of `urls`, `add` and `remove`, answers 400 `BAD_REQUEST`.
- `POST /webrequest/apply` answers `{"success": true, "urls": [...], "applied_via": "...", "rejected": [...]}`, or `{"success": true, "urls": [], "note": "nothing to apply"}` when the stored list is empty.
- A failed apply answers with a `code`: 409 for a state to wait out (`BACKTEST_RUNNING`, or `TERMINAL_BUSY` when the terminal shows a dialog such as a login), 503 when the terminal is not running (`PID_NOT_FOUND`, `WINDOW_NOT_FOUND`), 500 otherwise. While a backtest runs on the terminal, `PUT` and `verify` answer 409: the tester runs from the same `terminal64.exe`. The background re-apply waits for the run to end.
- `urls`, `add` and `remove` must be lists; anything else is a 400, never an empty list.
- `verify` opens the Options dialog for a moment, so a second call within 10 seconds answers 429 with `Retry-After`. Use it to check an apply, not to poll. On a bare-metal terminal it answers 409 `UNAVAILABLE`, because `common.ini` is the list there.

Inside the Windows VM every terminal starts with `/config:mt5start.ini`, and a terminal started that way never writes its settings back, so it forgets the list on every restart (the common.ini codec also cannot decode the lists builds 6238 and later write). So the list is set the way a user would, in Tools > Options > Expert Advisors, but with window messages sent straight to the dialog's controls ([`mt5api/chartctl/winoptions.py`](https://github.com/psyb0t/mt5-httpapi/blob/master/mt5api/chartctl/winoptions.py)): nothing is typed into whatever window has focus, no window has to be in front, and it works with no RDP or VNC viewer attached. It takes effect immediately in-session and takes seconds. OK is pressed only when every change took; otherwise the dialog is cancelled and nothing is saved. Because MT5 forgets the list on restart, the API re-applies the persisted list about 25 s after its own process starts, which covers the periodic auto-reboot, and the same delay after every terminal restart it performs itself (the health monitor's recovery restart, `POST /terminal/restart`). A terminal that starts again without the API restarting it (a LiveUpdate, a terminal the SDK started after a crash, one started by hand) is noticed within about 10 seconds: the API reconnects to it and re-applies the list after the same settle delay. That re-apply, like `POST /webrequest/apply`, skips a stored URL the terminal will not take and reports it under `rejected` instead of leaving the terminal with no list. `GET /webrequest` shows the running terminal (`terminal_pid`, `terminal_started_at`) and `stale: true` while it has not been given the stored list yet; after 5 minutes stale the API log shows `!!! WEBREQUEST LIST NOT RESTORED !!!` on every health pass. `POST /webrequest/apply` is for when that error stays. On a bare-metal terminal where `common.ini` *is* the store, it falls back to writing `common.ini` + restarting.

The desired list is persisted per terminal (`Config/webrequest.json`); the
first call migrates whatever the terminal already has, so manually configured
URLs are preserved. Every apply writes its outcome to `Config/webrequest_status.json`,
which `GET /webrequest` returns as `last_apply` (`at`, `reason`, `status`, `code`,
`rejected`, `pid`, `pid_started_at`). Since nothing depends on focus, terminals apply their lists in
parallel; each terminal's boot re-apply is still staggered by its port (25 s
plus 3 s times the port modulo 10) so one VM's terminals do not all open their
Options dialogs in the same second.
