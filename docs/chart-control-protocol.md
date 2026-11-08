---
title: "Chart Control Protocol"
description: "The file protocol between mt5-httpapi and the MT5ChartLoader EA: command files, loader duties, self-healing and custom loaders."
nav_order: 1
parent: "Chart Deployments"
---

# Chart Control Protocol v1

How mt5-httpapi's **Chart Deployments** feature attaches Expert Advisors to
charts remotely, keeps them running, and reports what's actually live,
without RDP, without restarting terminals, and without any orchestrator.

MT5 has no SDK call to attach an EA to a chart. The only programmatic path
is `ChartApplyTemplate()` from MQL5 code already running inside the
terminal. So the API and a small **resident loader EA** cooperate over a
handful of JSON files in the terminal's `MQL5\Files\chartctl\` sandbox.

The protocol is intentionally file-based so that:

- it needs zero WebRequest whitelist entries (works on locked-down terminals),
- it's trivially debuggable over RDP during rollout, and
- **any** EA can implement it: the bundled `MT5ChartLoader`, or your own
  resident utility EA (e.g. an account tracker) that adopts
  `ChartControl.mqh`.

---

## Roles

| Side | Writes | Reads |
|------|--------|-------|
| **API** (mt5-httpapi) | `desired.json`, `command.json`, generated `.tpl` files | `observed.json`, `command_result.json` |
| **Loader EA** (in terminal) | `observed.json`, `command_result.json`, `owned.json`, screenshots | `desired.json`, `command.json`, `owned.json` |

The API owns *desired state*. The loader owns *observed truth*. Neither
writes the other's files. Success is defined as **observed converging on
desired**, never as "the API copied some files."

---

## Files (all under `MQL5\Files\chartctl\`)

### `desired.json` (API → loader)

```json
{
  "protocol": 1,
  "revision": 42,
  "updated_at": "2026-07-09T12:00:00Z",
  "reconcile_interval": 5,
  "deployments": [
    {
      "id": "dep_a1b2c3",
      "expert": "HappyGoldScalp",
      "template": "\\Files\\chartctl\\dep_a1b2c3.tpl",
      "symbol": "XAUUSD",
      "timeframe": "M5",
      "enabled": true
    }
  ]
}
```

`revision` is a monotonic counter that the loader echoes back as
`applied_revision`. `reconcile_interval` is the API's
`chartctl.reconcile_hint_interval` in seconds, a hint for custom loaders; the
bundled loader ignores it and reconciles every second. `template` is passed verbatim to `ChartApplyTemplate()`;
the leading backslash makes MT5 resolve it against `<data>\MQL5`, the
only search root that doesn't depend on which EX5 hosts the loader
(paths without a leading backslash resolve relative to the calling EX5's
own folder, and the `templates\` GUI directory is never searched). The
API generates one `.tpl` per deployment, written into the
`MQL5\Files\chartctl\` protocol directory.

### `observed.json` (loader → API, rewritten every pass, ~1s)

```json
{
  "protocol": 1,
  "loader": { "name": "MT5ChartLoader", "version": "1.0.5",
              "last_loop": "2026-07-09 12:00:03", "applied_revision": 42 },
  "terminal": { "auto_trading": true },
  "charts": [
    { "chart_id": 133039117, "symbol": "XAUUSD", "timeframe": "PERIOD_M5",
      "expert": "HappyGoldScalp", "expert_enabled": true,
      "deployment_id": "dep_a1b2c3" }
  ],
  "deployments": [
    { "id": "dep_a1b2c3", "status": "running", "chart_id": 133039117 }
  ],
  "errors": []
}
```

`version` is `CHARTCTL_VERSION` in `ChartControl.mqh`. The loader reports
each desired deployment as `running` (with its `chart_id`), `pending` or
`paused`. Each failing deployment gets one `errors[]` entry, cleared when it
next attaches:

```json
{ "id": "dep_a1b2c3", "status": "failed", "code": "EXPERT_NOT_ATTACHED",
  "detail": "template applied but CHART_EXPERT_NAME empty after 10s ..." }
```

The codes are `SYMBOL_NOT_FOUND`, `CHART_OPEN_FAILED`,
`TEMPLATE_APPLY_FAILED` and `EXPERT_NOT_ATTACHED`.

`applied_revision == desired.revision` **and** deployment `status: running`
is the only definition of a converged deployment. The API's
`GET /deployments` merges the two files and derives per-deployment status
(`pending`, `running`, `degraded`, `failed`, `paused`; see
[Chart Deployments](chart-deployments.md#post-deployments)).

### `owned.json` (loader's own state)

```json
{ "owned": [ { "chart_id": 133039117, "deployment_id": "dep_a1b2c3" } ] }
```

Which open chart belongs to which deployment. The loader keeps this map in memory, writes it whenever it changes, and reads it back when it starts, so a reloaded loader still knows its charts. The API never reads it; `observed.json` already carries each chart's `deployment_id`.

### `command.json` / `command_result.json` (one-shot commands)

For operations that produce an artifact rather than converge state
(currently `screenshot` and `close_chart`; the loader also understands a
`reconcile` nudge, which the API does not send, because
`POST /deployments/reconcile` bumps the revision instead). One command in
flight, so a second API call meanwhile answers 409 `LOADER_BUSY`; the loader
writes the result keyed by `command_id` and deletes the command.

```json
{ "command_id": "cmd_1a2b3c4d", "action": "screenshot",
  "chart_id": 133039117, "width": 1280, "height": 720 }
{ "command_id": "cmd_1a2b3c4d", "status": "ok", "file": "cmd_1a2b3c4d.png" }
```

A screenshot lands in `MQL5\Files\chartctl\shots\<command_id>.png`. A failed
command answers `"status": "error"` with `error_code` (`SCREENSHOT_FAILED`,
`CLOSE_REFUSED`, `CLOSE_FAILED`, `UNKNOWN_ACTION`) and `error_detail`.

`close_chart` closes an arbitrary chart by `chart_id` (from the observed
charts list), the cleanup escape hatch for charts the loader cannot
attribute. The loader refuses (`CLOSE_REFUSED`) to close its own chart.

---

## Loader responsibilities

Every pass (timer-driven, ~1s), the owning loader:

1. Answers any pending `command.json`.
2. Reads `desired.json` and reconciles, on every pass whether or not
   `revision` changed. **Reconcile** = diff desired deployments against
   actual charts:
   - Enabled deployment with no chart in the ownership map → first try to **adopt** an unowned chart already running the exact expert + symbol + timeframe and record it in `owned.json`; only if none exists, `SymbolSelect` → `ChartOpen` → `ChartApplyTemplate` → verify `CHART_EXPERT_NAME` within 10s → record the chart in `owned.json`. A failed attach closes the chart it opened and backs off 60s.
   - Enabled deployment whose chart has no expert (removed, or its `OnInit` failed) → **re-arm**: `ChartApplyTemplate` on that same chart and verify, instead of opening another. A failed re-arm keeps the chart and backs off 60s.
   - A chart it owns whose deployment is gone or disabled → `ChartClose`, and the chart leaves the map.
   - Map entries for charts that no longer exist are dropped.
   - Charts it doesn't own are **reported but never touched** by
     reconcile (an explicit `close_chart` command can close them).
3. Writes `observed.json`.

### Rules

- The loader **never calls trade functions**. Chart lifecycle only.
- Exactly **one** loader per terminal, guarded by a terminal
  `GlobalVariable` mutex (`chartctl_loader_owner`). The owner touches it every
  pass. A second loader that finds it touched within 120 seconds stays
  passive, reclaiming only if the owner vanishes.
- All file writes are atomic (temp + `FileMove`).
- Attribution is by the `owned.json` map, never by anything on the chart itself. Loader 1.0.2 and earlier wrote `chartctl:<id>` into the chart comment, which any expert calling `Comment()` overwrites. MT5 gives the charts it restores from the saved profile new ids, so after a terminal restart the map's old entries are dropped and reconcile adopts the exact-match restored charts instead. Experts on restored charts load a few seconds after the terminal starts, so for its first 30 seconds the loader only adopts and opens no new charts; opening one earlier would duplicate a chart that is still loading.
- MT5 writes a chart's expert into the saved profile only when it exits cleanly. After a hard stop (a killed terminal, `POST /terminal/restart`, the health monitor's recovery, a VM restart) it restores a deployment's chart as the profile had it when the loader opened it, without the expert. So when the 30 seconds end, a deployment that was in `owned.json` at startup and still has no chart re-arms an unowned expert-less chart on its symbol and timeframe, once. After that pass the loader never touches an unowned expert-less chart.
- A chart leaves the map only once `ChartClose` succeeds.
- MT5 only loads an `.ex5` that was on disk when the terminal started, or that a Navigator refresh has picked up since. `POST /experts` runs that refresh inside the Windows VM; see [Chart Deployments](chart-deployments.md).

---

## Self-healing

Two layers converge a terminal back to desired state after any restart
(including the optional `reboot_interval` VM reboots):

1. **MT5 native chart restoration** brings back charts + attached experts
   from the last saved profile. Often that covers everything, with no work
   from the loader.
2. **Loader reconciliation** repairs whatever native restoration missed
   (a hard stop that restored a chart without its expert, chart closed by
   hand, failed `OnInit`).

Neither layer helps when the loader itself is not running. Nothing alerts on
that; `GET /loader` answers `alive: false` once `observed.json` is older than
`chartctl.observed_stale_after`.

---

## Implementing the protocol in your own EA

The whole loader is a portable include. In your resident EA:

```mql5
#include <ChartControl.mqh>
CChartControl ctl;

int OnInit()  { if(!ctl.Init()) return INIT_FAILED;
                EventSetTimer(1); return INIT_SUCCEEDED; }
void OnTimer(){ ctl.Tick(); }
void OnDeinit(const int r){ ctl.Deinit(); }
```

That's it. Your account tracker (or any always-on EA) becomes the loader,
so one resident EA does telemetry *and* chart deployment instead of two. The
mutex makes running both your EA and the standalone `MT5ChartLoader`
degrade safely. See [`assets/experts/MT5ChartLoader.mq5`](https://github.com/psyb0t/mt5-httpapi/blob/master/assets/experts/MT5ChartLoader.mq5) for the reference
glue and [`assets/experts/include/ChartControl.mqh`](https://github.com/psyb0t/mt5-httpapi/blob/master/assets/experts/include/ChartControl.mqh) for the implementation.

---

## Bootstrapping the loader

**Zero-touch (default).** Provisioning does everything. No RDP, no manual
attach, fully API/config-driven:

1. On VM boot, `start.bat` runs `compile-chartctl-loader.bat`, which copies
   `ChartControl.mqh` + `MT5ChartLoader.mq5` into every broker base
   terminal, compiles with that base's MetaEditor64, and propagates the
   `.ex5` into every existing terminal instance (new instances inherit it
   from the base copy).
2. `config_helper.py` writes a `[StartUp] Expert=Advisors\MT5ChartLoader`
   section into each terminal's generated `mt5start.ini`, with
   `Symbol=EURUSD` plus the terminal's `symbol_suffix` (e.g. `EURUSD.r`) and
   `Period=H1`, so the terminal attaches the loader itself at every launch.
3. The `[StartUp]` line re-fires on every launch (including the periodic
   `reboot_interval` reboots); the GlobalVariable mutex makes this
   idempotent. A duplicate loader closes its own chart and vanishes, so
   charts never accumulate.

Both steps honor the same gating as the API: live-mode terminals only,
`chartctl.enabled` globally, per-terminal `chartctl: false` opts out.

**Manual (fallback / existing fleets).** Drag `MT5ChartLoader` onto any
chart once over RDP, or fold `ChartControl.mqh` into a resident EA you
already deploy.

---

## Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/experts` | Upload `.ex5` (multipart) |
| `GET` | `/experts` | List staged experts |
| `DELETE` | `/experts/<name>` | Remove staged expert (refused if in use) |
| `POST` | `/sets` | Upload `.set` (returns parsed inputs) |
| `GET` | `/sets`, `/sets/<name>` | List / inspect set files |
| `DELETE` | `/sets/<name>` | Remove staged set (refused if in use) |
| `POST` | `/deployments` | Declare a deployment (EA+set+symbol+TF) |
| `GET` | `/deployments`, `/deployments/<id>` | Desired ⋈ observed status |
| `PATCH` | `/deployments/<id>` | Change set file or pause/resume |
| `DELETE` | `/deployments/<id>` | Remove a deployment |
| `POST` | `/deployments/reconcile` | Force the loader to re-reconcile |
| `GET` | `/charts` | Live chart/EA inventory from the terminal |
| `GET` | `/loader` | Loader presence/version/liveness |
| `POST` | `/charts/<chart_id>/screenshot` | PNG of a chart |
| `POST` | `/charts/<chart_id>/close` | Close a chart (incl. unattributed ones) |

All routes sit behind the terminal's existing per-account route prefix and
bearer-token auth. Nothing here touches the MT5 SDK lock.
