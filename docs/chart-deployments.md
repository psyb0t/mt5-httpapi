# Chart Deployments — remote EA deployment over HTTP

Attach Expert Advisors to charts with set files over the HTTP API. No RDP, no
terminal restart, and no human clicking through the Navigator.

## Contents

- [Quick start](#quick-start)
- [`POST /deployments`](#post-deployments)
- [`GET /loader`](#get-loader)
- [`GET /charts`](#get-charts)
- [Screenshots](#post-chartschart_idscreenshot) and [closing charts](#post-chartschart_idclose)
- [WebRequest allowlist](#webrequest-allowlist)
- [Chart Control Protocol](chart-control-protocol.md) — the file contract the loader speaks


> 📹 **Video walkthrough:** *Coming soon — a full walkthrough of staging, deploying, and
> debugging EAs via the chartctl API.*

Attach Expert Advisors to charts with set files over the HTTP API — no RDP,
no terminal restart. Stage an `.ex5` + `.set`, declare a deployment (expert +
set + symbol + timeframe), and a resident loader EA inside the terminal
reconciles the terminal's actual charts to match. The API holds *desired
state*; the loader reports *observed truth* back, so a deployment only reads
`running` once the expert is confirmed live on a chart.

Any client — a dashboard, a script, an AI agent — drives it over plain REST.
Full protocol contract and file formats: [`docs/chart-control-protocol.md`](docs/chart-control-protocol.md).

| Method                                           | Endpoint                              | Description                                              |
| ------------------------------------------------ | ------------------------------------- | -------------------------------------------------------- |
| `POST` / `GET` / `DELETE`                        | `/experts` `/experts/<hash>`          | Stage, list, remove EA `.ex5` files                      |
| `POST` / `GET`                                   | `/sets` `/sets/<name>`                | Stage, list, inspect `.set` parameter files (parsed)     |
| `POST` / `GET`                                   | `/deployments`                        | Create or list deployments                               |
| `GET` / `PATCH` / `DELETE`                       | `/deployments/<id>`                   | Inspect, pause/resume/change set, tear down a deployment |
| `POST`                                           | `/deployments/reconcile`              | Force an immediate reconcile cycle (otherwise periodic)  |
| `GET`                                            | `/charts`                             | Live chart/EA inventory from inside the terminal         |
| `GET`                                            | `/loader`                             | Loader EA status — alive, version, chart open count      |
| `POST`                                           | `/charts/<chart_id>/screenshot`       | Capture a chart PNG from inside the terminal             |
| `POST`                                           | `/charts/<chart_id>/close`            | Close a chart by id (any chart, including leaks)         |

**Opt-in.** `chartctl.enabled` defaults to `false`; set it to `true` in
`config.yaml` to switch the feature on. Live-mode terminals only, and any single
terminal can stay clear of it with `chartctl: false`.

That default is deliberate. Enabling this writes a `[StartUp]` section into every
live terminal's INI, so the loader EA attaches itself at launch — fleet-wide
behaviour that must be asked for, never inherited from an upgrade. With the block
absent nothing changes and the endpoints return 404.

**Once enabled, setup is none.** On boot, provisioning auto-compiles the bundled loader EA
(`assets/experts/MT5ChartLoader.mq5`) in every broker base and wires a
`[StartUp] Expert=` line into each live terminal's `mt5start.ini`, so the
loader attaches itself at launch — the whole path is API/config-driven, no
RDP. Already have a resident utility EA on every terminal? Adopt the protocol
into it with three calls instead of running a second EA — see
`assets/experts/include/ChartControl.mqh`. The standalone loader steps aside
automatically (single-loader mutex via terminal GlobalVariable).

### Quick start

```bash
# 0. Set your base URL + auth token
export MT5_API_URL=http://localhost:8888/yourbroker/yourlogin
export MT5_API_TOKEN=$(grep api_token config/config.yaml | awk -F'"' '{print $2}')

# 1. Stage an expert (.ex5) and a set file (.set)
curl -F "expert=@HappyGoldScalp.ex5" "$MT5_API_URL/experts"
curl -F "set=@gold-m5.set"          "$MT5_API_URL/sets"   # returns parsed inputs

# 2. Deploy — declare desired state
curl -X POST "$MT5_API_URL/deployments" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"expert":"HappyGoldScalp.ex5","set":"gold-m5.set","symbol":"XAUUSD","timeframe":"M5"}'
# -> {"id":"dep_a1b2c3","status":"pending"}

# 3. Verify — status flips to "running" once the loader confirms attach
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/deployments"

# 4. Change the set file in place (no restart), pause, or tear down
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -d '{"set":"gold-m5-v2.set"}'
curl -X PATCH  "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -d '{"enabled":false}'
curl -X DELETE "$MT5_API_URL/deployments/dep_a1b2c3" \
  -H "Authorization: Bearer $MT5_API_TOKEN"
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

If `alive` is `false`, the loader hasn't started yet — check that
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
(`image/png`). No query-string auth — put the token in the header.

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
| `GET`  | `/webrequest`             | Current effective allowlist           |
| `PUT`  | `/webrequest`             | Replace or add to the allowlist       |
| `POST` | `/webrequest/apply`       | Re-apply current desired list now     |

```bash
curl -H "Authorization: Bearer $MT5_API_TOKEN" "$MT5_API_URL/webrequest"
curl -X PUT "$MT5_API_URL/webrequest" \
  -H "Authorization: Bearer $MT5_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"add":["https://api.telegram.org"]}'   # or {"urls":[...]} to replace
# -> {"success":true,"urls":[...],"applied_via":"autoit:OK"}
curl -X POST "$MT5_API_URL/webrequest/apply" \
  -H "Authorization: Bearer $MT5_API_TOKEN"   # re-apply current list now
```

Inside the Windows VM the allowlist is **not** stored in `common.ini` — it
lives in the machine-bound `MQL5\experts.dat` and MT5 drops it on every
restart. So the list is applied the way a user would: a bundled AutoIt
interpreter (`assets/autoit/`; unmodified official binary, redistributed
with its EULA and notices — see `assets/autoit/NOTICE.txt`) drives
Tools → Options → Expert Advisors and types the URLs in. This takes effect immediately in-session (no restart), and
because MT5 forgets it on restart, the API re-applies the persisted list
automatically ~25 s after each terminal (re)start, so it survives the periodic
auto-reboot. On a bare-metal terminal where `common.ini` *is* the store, it
falls back to writing `common.ini` + restarting.

The desired list is persisted per terminal (`Config/webrequest.json`); the
first call migrates whatever the terminal already has, so manually configured
URLs are preserved. GUI applies are serialized across every terminal on a host
by a named Windows kernel mutex (crash-safe — a dead holder is auto-released),
and each terminal's boot re-apply is staggered by its port, so many terminals
per VM can safely provision WebRequest URLs without their keystrokes colliding.
