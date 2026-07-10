# Chart Control Protocol v1

How mt5-httpapi's **Chart Deployments** feature attaches Expert Advisors to
charts remotely, keeps them running, and reports what's actually live —
without RDP, without restarting terminals, and without any orchestrator.

MT5 has no SDK call to attach an EA to a chart. The only programmatic path
is `ChartApplyTemplate()` from MQL5 code already running inside the
terminal. So the API and a small **resident loader EA** cooperate over a
handful of JSON files in the terminal's `MQL5\Files\chartctl\` sandbox.

The protocol is intentionally file-based so that:

- it needs zero WebRequest whitelist entries (works on locked-down terminals),
- it's trivially debuggable over RDP during rollout, and
- **any** EA can implement it — the bundled `MT5ChartLoader`, or your own
  resident utility EA (e.g. an account tracker) that adopts
  `ChartControl.mqh`.

---

## Roles

| Side | Writes | Reads |
|------|--------|-------|
| **API** (mt5-httpapi) | `desired.json`, `command.json`, generated `.tpl` files | `observed.json`, `command_result.json` |
| **Loader EA** (in terminal) | `observed.json`, `command_result.json`, screenshots | `desired.json`, `command.json` |

The API owns *desired state*. The loader owns *observed truth*. Neither
writes the other's files. Success is defined as **observed converging on
desired**, never as "the API copied some files."

---

## Files (all under `MQL5\Files\chartctl\`)

### `desired.json` — API → loader

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
      "template": "chartctl\\dep_a1b2c3.tpl",
      "symbol": "XAUUSD",
      "timeframe": "M5",
      "enabled": true
    }
  ]
}
```

`revision` is a monotonic counter; the loader can skip a full parse when it
hasn't changed. `template` is relative to the terminal's `templates\`
directory — the API generates one `.tpl` per deployment.

### `observed.json` — loader → API (rewritten every reconcile pass, ~5s)

```json
{
  "protocol": 1,
  "loader": { "name": "MT5ChartLoader", "version": "1.0.0",
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

`applied_revision == desired.revision` **and** deployment `status: running`
is the only definition of a converged deployment. The API's
`GET /deployments` merges the two files and derives per-deployment status
(`pending → running → degraded → failed → paused`).

### `command.json` / `command_result.json` — one-shot imperatives

For operations that produce an artifact rather than converge state
(currently `screenshot`, and a `reconcile` nudge). One command in flight;
the loader writes the result keyed by `command_id` and deletes the command.

---

## Loader responsibilities

Every pass (timer-driven, ~1s), the owning loader:

1. Reads `desired.json`; if `revision` changed, reconciles.
2. **Reconcile** = diff desired deployments against actual charts:
   - Enabled deployment with no matching chart → `SymbolSelect` →
     `ChartOpen` → `ChartApplyTemplate` → verify `CHART_EXPERT_NAME`
     within 10s → stamp `chartctl:<id>` into the chart comment for
     cross-restart attribution.
   - A chart it owns (comment starts `chartctl:`) whose deployment is gone
     or disabled → `ChartClose`.
   - Charts it doesn't own are **reported but never touched**.
3. Writes `observed.json`.
4. Answers any pending `command.json`.

### Rules

- The loader **never calls trade functions**. Chart lifecycle only.
- Exactly **one** loader per terminal, guarded by a terminal
  `GlobalVariable` mutex (`chartctl_loader_owner`). A second loader detects
  the live mutex and stays passive, reclaiming only if the owner vanishes.
- All file writes are atomic (temp + `FileMove`).
- Attribution is by the `chartctl:<id>` chart comment the loader sets at
  attach time; MT5 persists it with the saved chart, so after a restart the
  loader re-identifies its charts and only repairs what's missing.

---

## Self-healing

Three layers converge a terminal back to desired state after any restart
(including the optional `reboot_interval` VM reboots):

1. **MT5 native chart restoration** brings back charts + attached experts
   from the last saved profile — often everything, for free.
2. **Loader reconciliation** repairs whatever native restoration missed
   (crash before profile save, chart closed by hand, failed `OnInit`).
3. **Watchdog** (`monitor.py`) logs loudly if the terminal is alive but the
   loader's `last_loop` goes stale — the one state layers 1–2 can't fix
   alone.

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

That's it — your account tracker (or any always-on EA) becomes the loader,
so one resident EA does telemetry *and* chart deployment instead of two. The
mutex makes running both your EA and the standalone `MT5ChartLoader`
degrade safely. See `assets/experts/MT5ChartLoader.mq5` for the reference
glue and `assets/experts/include/ChartControl.mqh` for the implementation.

---

## Bootstrapping the loader

**Zero-touch (default).** Provisioning does everything — no RDP, no manual
attach, fully API/config-driven:

1. On VM boot, `start.bat` runs `compile-chartctl-loader.bat`, which copies
   `ChartControl.mqh` + `MT5ChartLoader.mq5` into every broker base
   terminal, compiles with that base's MetaEditor64, and propagates the
   `.ex5` into every existing terminal instance (new instances inherit it
   from the base copy).
2. `config_helper.py` writes a `[StartUp] Expert=Advisors\MT5ChartLoader`
   section into each terminal's generated `mt5start.ini` — so the terminal
   attaches the loader itself at every launch. The startup chart symbol
   honors the terminal's `symbol_suffix` (e.g. `EURUSD.r`).
3. The `[StartUp]` line re-fires on every launch (including the periodic
   `reboot_interval` reboots); the GlobalVariable mutex makes this
   idempotent — a duplicate loader closes its own chart and vanishes, so
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
| `POST` | `/deployments` | Declare a deployment (EA+set+symbol+TF) |
| `GET` | `/deployments`, `/deployments/<id>` | Desired ⋈ observed status |
| `PATCH` | `/deployments/<id>` | Change set file or pause/resume |
| `DELETE` | `/deployments/<id>` | Remove a deployment |
| `POST` | `/deployments/reconcile` | Force the loader to re-reconcile |
| `GET` | `/charts` | Live chart/EA inventory from the terminal |
| `GET` | `/loader` | Loader presence/version/liveness |
| `POST` | `/charts/<chart_id>/screenshot` | PNG of a chart |

All routes sit behind the terminal's existing per-account route prefix and
bearer-token auth. Nothing here touches the MT5 SDK lock.
