---
title: "Backtesting"
description: "Run Strategy Tester jobs over HTTP: build the tester files, start and cancel jobs, poll their status and download the reports."
nav_order: 5
has_children: true
---

# Backtesting without babysitting MT5

Build Strategy Tester files, launch the job, watch it run, and pull the reports back over HTTP instead of spending your life clicking around inside MT5.

## Contents

- [Backtest API](#backtest-api)
- [Build a set file](#post-backtestbuild-set)
- [Asset sources](#asset-sources)
- [Regenerate the warm-up expert](#regenerating-mt5systemwarmupex5)
- [Build an INI](#post-backtestbuild-ini)
- [Submit a job](#post-backtest)
- [Poll job status](#get-backtestjob_id)
- [Download report and log](#get-backtestjob_idreport--log)
- [Read live diagnostic tails](#get-backtestjob_idtail)
- [Cancel a job](#post-backtestjob_idcancel)
- [Scheduled reboots](#scheduled-reboots)
- [Worked example](#worked-example)
- [Optimization example](#optimization-example)
- [Optimization guide](backtest-optimization.md)
- [Multi-VM scaling](multi-vm-setup.md)

## Backtest API

This is the whole Strategy Tester pipeline over HTTP: build the ugly MT5 files, submit them, poll the job, then grab the useful shit when it finishes.
Responses below use snake_case field names. The old camelCase names still
show up alongside them in responses, and request bodies still accept either
one, but the camelCase names are deprecated and go away in v5.0.0.
`POST /backtest` execution requires a terminal whose `config.yaml` entry has
`mode: backtest`. The two stateless builders, `POST /backtest/build-ini` and
`POST /backtest/build-set`, work in either mode because they only transform
JSON into text and never launch MT5. The execution restriction is structural: MT5 is
single-instance per portable data directory, so if `terminal64.exe` is already
running to back the live SDK, a Strategy Tester subprocess against the same
directory exits silently with code `0` and produces no report. `mode: backtest`
skips the auto-launch and the live-mode SDK init, leaving the data dir free
for the tester. Pick the broker/account namespace whose credentials you want
injected into the run's `[Common]` section — e.g. a dedicated
`darwinex/tester` entry next to your live `darwinex/main`.

If your broker uses suffixed symbols like `EURUSDp` or `EURUSD.p`, set
`terminals[].symbol_suffix` on that backtest terminal. If the broker uses plain
symbols, set `symbol_suffix: ""` explicitly.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/backtest/build-ini` | Build a complete `tester.ini` from JSON. |
| POST | `/backtest/build-set` | Build an MT5 `.set` parameter file from JSON. |
| POST | `/backtest` | Submit an INI plus expert and optional set file. |
| GET | `/backtest/<job_id>` | Poll job state and parsed results. |
| GET | `/backtest/<job_id>/report` | Download the raw MT5 report. |
| GET | `/backtest/<job_id>/log` | Download the terminal log. |
| GET | `/backtest/<job_id>/tail` | Read live diagnostic tails while a job runs. |
| POST | `/backtest/<job_id>/cancel` | Stop a queued or running job and mark it cancelled. |

For a plain backtest, leave `[Tester].Optimization=0` or omit it. For an
optimization, set `[Tester].Optimization` to one of:

- `1` — slow complete algorithm
- `2` — fast genetic algorithm
- `3` — all symbols selected in Market Watch

Optimization runs require a `.set` file whose input parameters already contain
optimization ranges. mt5-httpapi can now either stage an MT5-saved `.set`
directly or generate one from structured JSON via `POST /backtest/build-set`.

Optimization modes do not all emit the same MT5 artifacts:

| Mode | MT5 setting | Search scope | Primary parsed artifact | Report name written by MT5 |
| ---- | ----------- | ------------ | ----------------------- | -------------------------- |
| `1`  | slow complete | Single symbol in `[Tester].Symbol` | MT5 XML spreadsheet report | `<report>.xml` |
| `2`  | genetic | Single symbol in `[Tester].Symbol` | MT5 XML spreadsheet report | `<report>.xml` |
| `3`  | all Market Watch symbols | Symbols currently selected in Market Watch | `Tester/cache/*.opt` cache file | `<report>.symbols.xml` |

Mode `3` is the odd bastard out. MT5 still writes a report file, but it is the
header-only `.symbols.xml` variant and the actual pass rows live in the tester
cache. mt5-httpapi parses that cache, recovers pass-to-symbol mappings from the
agent logs, and exposes the discovered cache artifact in `optimization_cache`.
If you want the full mode-by-mode request/response examples, see
[`docs/backtest-optimization.md`](backtest-optimization.md).

### `POST /backtest/build-set`

MT5 `.set` files are plain parameter files, typically UTF-16 text. A normal
saved set looks like this:

```ini
Properties_=------
Magic_Number=1615044595
Entry_Amount=0.01
Stop_Loss=0
Take_Profit=92
___0______=------
Ind0Param0=3
Ind0Param1=1
Ind0Param2=1
Ind0Param3=8.0
___1______=------
Ind1Param0=20
Ind1Param1=31
Ind1Param2=5
```

That example matches the structure of the bundled preset under [`assets/sets/`](https://github.com/psyb0t/mt5-httpapi/tree/master/assets/sets):
simple `name=value` lines plus separator keys.

For optimization, MT5 UI exports each optimizable input in this form:

```ini
property=value||start||step||stop||Y|N
```

Meaning:

| Position | Meaning | Notes |
| -------- | ------- | ----- |
| 1 | Current value | Default or starting value |
| 2 | Start | Optimization range start |
| 3 | Step | Increment per pass |
| 4 | Stop | Optimization range end |
| 5 | Optimize | `Y` = enabled, `N` = disabled |

Examples:

```ini
TakeProfit=50||10||5||100||Y
StopLoss=30||10||5||80||Y
LotSize=0.1||0||0||0||N
```

- `TakeProfit` and `StopLoss` are optimized because the `optimize` field is `Y`.
- `LotSize` stays fixed because the `optimize` field is `N`.

So a parameter you want optimized with a known range looks like this:

```ini
MyParam=50||10||5||200||Y
```

And a parameter you want fixed looks like this:

```ini
MyParam=50||0||0||0||N
```

Create the set file from the MT5 Strategy Tester "Inputs" tab after enabling
optimization ranges for the parameters you want to vary, then save it.
If you already have structured parameter metadata, `POST /backtest/build-set`
accepts JSON and returns MT5-native `.set` text using the same `Y` / `N`
markers.

Example JSON for `POST /backtest/build-set`:

```json
{
  "comments": [
    "saved on 2026.05.15 08:30:02",
    "this file contains input parameters for testing/optimizing MyEA"
  ],
  "parameters": [
    {"name": "_Properties_", "value": "------"},
    {
      "name": "Take_Profit",
      "value": 92,
      "start": 80,
      "step": 4,
      "stop": 92,
      "optimize": true
    },
    {
      "name": "Stop_Loss",
      "value": 0,
      "start": 0,
      "step": 1,
      "stop": 10,
      "optimize": false
    }
  ]
}
```

The response is `text/plain` `.set` content ready to save or upload: UTF-8
with LF line endings, each `comments` line prefixed with `; ` unless it
already starts with `;`. An entry without `start`, `step`, `stop` and
`optimize` becomes a plain `name=value` line; an entry with only some of
them is refused with `400`.

Only one tester runs at a time per API process (serialized by an internal lock);
additional submissions queue.

If the API restarts while a job is queued or running, startup recovery marks
that orphaned job as `failed` with `API restarted before completion`. Recovery
runs in a background thread so a large job history can never delay the API from
serving, and only inspects jobs touched within the last `BACKTEST_SWEEP_LOOKBACK`
(default `24h`). On a `mode: backtest` terminal the same startup pass also
kills that terminal's leftover `terminal64.exe` and `metatester64.exe`
processes. Pruning runs in that startup pass too, but only once the shared job
directory holds 1000+ entries, and at most once a day per VM: finished jobs
(`completed`, `failed`, `cancelled`) older than `BACKTEST_JOB_RETENTION`
(default `30d`) lose their state file and staging dir, so their
status/report/log URLs return 404 from then on.

### Asset sources

Send the expert and set file inline for one-off runs, or dump reusable ones in
the host-managed pool:

```
assets/
  experts/   # *.ex5 — host-managed expert advisors (mounted read-only)
  sets/      # *.set — host-managed parameter files
```

The `docker-compose.yml` mount `./assets:/shared/assets:ro` exposes them inside
the VM so the API can read them. Path traversal in `expert_name` / `set_name`
is rejected.

The repository ships the warm-up EA source:

```text
assets/experts/MT5SystemWarmup.mq5
```

Compile it inside Windows to produce the untracked
`assets/experts/MT5SystemWarmup.ex5` used by historical warm-up flows.

### Regenerating `MT5SystemWarmup.ex5`

With the VM running and scripts synced, open noVNC, launch `cmd.exe`, and run:

```bat
C:\Users\Docker\Desktop\Shared\scripts\compile-warmup-ea.bat
```

What the script does:

- locates the first installed broker `base` terminal under `C:\Users\Docker\Desktop\Shared\terminals\*\base`
- copies `C:\Users\Docker\Desktop\Assets\experts\MT5SystemWarmup.mq5` into that terminal's `MQL5\Experts\Advisors\`
- runs `MetaEditor64.exe /compile:... /log:...`
- copies the resulting `MT5SystemWarmup.ex5` back into `C:\Users\Docker\Desktop\Assets\experts\`

Compile log lands at:

```text
C:\Users\Docker\Desktop\Shared\logs\compile-warmup-ea.log
```

If the `Assets` folder is not exposed at `C:\Users\Docker\Desktop\Assets`,
the script falls back to `C:\Users\Docker\Desktop\Shared\assets`.

### `POST /backtest/build-ini`

Body (JSON):

| Field              | Required | Notes                                              |
| ------------------ | -------- | -------------------------------------------------- |
| `symbol`           | yes      | e.g. `NZDJPY`                                      |
| `timeframe`        | yes      | `M1` `M5` `M15` `H1` `D1` … (21 standard values)   |
| `expert`           | yes      | filename ending in `.ex5` (no path)                |
| `from_date`+`to_date`| one of   | `YYYY-MM-DD`                                       |
| `last_years`        | one of   | integer; window ends today UTC                     |
| `last_days`         | one of   | integer                                            |
| `modelling`        | no       | `every-tick` `1m-ohlc` `open-prices` `real-ticks` (or MT5's `0` `1` `2` `4`); default `open-prices` |
| `latency_ms`        | no       | integer milliseconds, written as `ExecutionMode`; default `0` |
| `deposit`          | no       | default `10000`, must be > 0                       |
| `currency`         | no       | default `USD`, upper-cased                         |
| `leverage`         | no       | default `100`, must be > 0, written as `1:N`       |
| `expert_parameters` | no       | `.set` filename (no path)                          |
| `optimization`     | no       | `0` off, `1` slow complete, `2` genetic, `3` Market Watch symbols |
| `optimization_criterion` | no  | `0..7`; default `0` (max balance)                 |
| `forward_mode`      | no       | `0..4`; default `0`                               |
| `visual`           | no       | truthy enables visual tester mode; default off    |
| `report_name`       | no       | default `backtest-report.htm` for backtests, `optimization-report.xml` for optimizations; `.htm` or `.xml` is appended when missing |

Returns `text/plain` with the generated INI. Both builders need
`Content-Type: application/json` and answer `400` with an `error` string for a
bad body.

Example JSON for an optimization INI:

```json
{
  "symbol": "GBPUSD",
  "timeframe": "M15",
  "expert": "MyEA.ex5",
  "last_years": 3,
  "modelling": "open-prices",
  "expert_parameters": "myea-optimizer.set",
  "optimization": 2,
  "optimization_criterion": 5,
  "report_name": "gbpusd-m15-sharpe-search"
}
```

### `POST /backtest`

Multipart form fields:

| Field          | Required | Notes                                                          |
| -------------- | -------- | -------------------------------------------------------------- |
| `ini`          | yes      | INI file (file upload)                                         |
| `expert`       | one of   | `.ex5` upload                                                  |
| `expert_name`  | one of   | filename in [`assets/experts/`](https://github.com/psyb0t/mt5-httpapi/tree/master/assets/experts)                                  |
| `set`          | no       | `.set` upload                                                  |
| `set_name`     | no       | filename in `assets/sets/`                                     |
| `top_passes`    | no       | For optimization jobs, keep the top `1..500` parsed passes (cache or XML) in the status payload. Default `50`. |
| `timeout`      | no       | Duration string override (`"30m"`, `"6h"`, `"3h30m"`; a bare number means hours). Defaults to the `BACKTEST_TIMEOUT` env var, then `backtest_timeout` from `config.yaml`, then hardcoded `6h`. Must be positive and at most `BACKTEST_MAX_TIMEOUT` (env, default `48h`): out-of-range values are rejected with `400` rather than silently clamped, since the terminal's run lock is held for the whole timeout. |

The INI upload must be UTF-8 text (a BOM is fine). An INI saved as UTF-16 is
refused with `400` (the API itself converts it to UTF-16 for MT5). When both an upload and an asset name are
sent, the upload wins. The whole multipart body is capped at 25 MiB
(`MAX_UPLOAD_BODY_BYTES`).

Responds `202 Accepted` with `Retry-After` header and the queued job payload:

```json
{
  "job_id": "b3f7…",
  "status": "queued",
  "broker": "darwinex",
  "account": "tester",
  "submitted_at": "2026-05-12T10:00:00Z",
  "started_at": null,
  "finished_at": null,
  "duration_seconds": null,
  "report_name": "backtest-report.htm",
  "status_url": "/backtest/b3f7…",
  "report_url": "/backtest/b3f7…/report",
  "log_url": "/backtest/b3f7…/log",
  "poll_after_seconds": 60,
  "optimization_type": 0,
  "optimization_results": null,
  "optimization_cache": null,
  "queue_position": 0
}
```

`queue_position` is 0-based and counts this process's queued and running jobs
in submission order, so `0` means the job runs next (or is running now). It
is left out once the job is finished.

`[Common]` `Login` / `Password` / `Server` in the uploaded INI are always
overwritten with the credentials from `config.yaml` for the request's
broker/account. The expert path is rewritten to `Uploaded\<basename>` and the
set file is namespaced per job to avoid collisions. The API also rewrites:

- `[Tester].Report` to `Reports\<basename>`. An empty one becomes
  `backtest-<hex>.htm` or `optimization-<hex>.xml`, and `.htm` or `.xml` is
  appended when missing. `ReplaceReport=1` and `ShutdownTerminal=1` are forced.
- `[Tester].Symbol`: the terminal's `symbol_suffix` is appended, unless the
  terminal's cached broker symbol list has the bare name and lacks the
  suffixed one.

Errors from `POST /backtest` are JSON with an `error` string:

| Status | When |
| --- | --- |
| `400` | missing `ini` file; INI not UTF-8; INI without `[Tester]`; `Optimization` not `0..3`; expert not `.ex5` or set not `.set`; asset name not found or a path; bad `timeout` or `top_passes`; `top_passes` and `topPasses` with different values; broker/account missing from `config.yaml` |
| `413` | multipart body over 25 MiB |
| `500` | `terminal64.exe` not found for this terminal |
| `503` | `code: WORKER_START_FAILED`: the worker thread could not start; the job is stored as `failed` with `Backtest worker could not be started` |

When the broker's server ships a new MT5 build, a launch of a terminal with
the update pending starts an updater (`<AppData>\MetaQuotes\Terminal\<hash>\liveupdate\terminal64.exe /update`)
and exits. If the next test relaunches the terminal before the updater has
replaced `terminal64.exe`, the updater never finishes. So before every launch
the API waits up to 120 s for a leftover updater of that terminal to exit,
then stops it, and a timed-out run's cleanup stops them too. Left alone they
pile up, each holding about 48 GDI and 36 USER objects, until the Windows
session runs out (65,536 GDI objects) and every terminal launch fails with
exit 10053 or 10012 ("not enough handles to start the platform").

### `GET /backtest/{job_id}`

Status payload. `status` ∈ `queued` `running` `completed` `failed` `cancelled`. When
completed, includes a `summary` object parsed from the HTML report
(`net_profit`, `profit_factor`, `recovery_factor`, `expected_payoff`, `sharpe_ratio`,
`max_drawdown`, `total_trades`, `profit_trades`, `loss_trades`, …).

For optimization jobs, the payload instead includes:

- `optimization_type`: the submitted MT5 optimization mode (`1`, `2`, or `3`)
- `optimization_results`: a parsed top-N list sorted by `Result` descending
- `optimization_cache`: cache artifact metadata when results came from an MT5 `.opt` cache file

Result source depends on the submitted mode:

- Every mode first looks for a matching `.opt` file in `Tester/cache/` and parses it when it can.
- Modes `1` and `2` fall back to the MT5 XML spreadsheet report when the cache gives no rows, which is the usual case for them.
- Mode `3` never parses the XML, because the `.symbols.xml` report does not contain the optimization rows.

For rows parsed from the XML report, the API keeps the MT5 column names as-is.
If the XML export includes columns such as `Profit`, `Profit Factor`,
`Expected Payoff`, `Drawdown`, `Recovery Factor`, `Sharpe Ratio`, or optimized
input names, those same fields appear in each `optimization_results` row.
Rows parsed from the cache have a fixed set of columns (see the mode-3
example below).

A job that ends `failed` or `cancelled` carries an `error` string:

| `error` starts with | Meaning |
| --- | --- |
| `Backtest timed out after Ns` | the run hit its `timeout`; leftover tester processes were killed (count appended) |
| `terminal64.exe exited with code N` | non-zero exit, followed by the terminal log tail when there is one |
| `Report not generated` | MT5 exited cleanly without a report, which is what a run on a terminal without `mode: backtest` ends in (the API does not check the mode) |
| `Backtest produced empty report (Bars=0, Ticks=0, Symbols=0)` | MT5 had no market data; the payload keeps `summary` |
| `Backtest crashed: ...` / `Backtest worker crashed before the run finished: ...` | an exception in the run or the worker |
| `Cannot prepare terminal directories: ...` | `Reports`, `MQL5\Experts\Uploaded` or `MQL5\Profiles\Tester` could not be created |
| `API restarted before completion` | startup recovery (see above) |
| `Cancelled by request` | status `cancelled` |

Example optimization status payload:

```json
{
  "job_id": "8c2a…",
  "status": "completed",
  "broker": "darwinex",
  "account": "tester",
  "report_name": "gbpusd-m15-sharpe-search.xml",
  "report_url": "/backtest/8c2a…/report",
  "log_url": "/backtest/8c2a…/log",
  "optimization_type": 2,
  "optimization_cache": null,
  "optimization_results": [
    {
      "Pass": 184,
      "Result": 2.41,
      "Profit": 1263.5,
      "Profit Factor": 1.48,
      "Expected Payoff": 13.02,
      "Recovery Factor": 3.11,
      "Total trades": 97,
      "Sharpe Ratio": 2.41,
      "FastPeriod": 12,
      "SlowPeriod": 34
    }
  ]
}
```

Example mode-3 optimization payload. Cache rows always have the columns
`Pass`, `Symbol`, `Result`, `Profit`, `Profit Factor`, `Expected Payoff`,
`Recovery Factor`, `Sharpe Ratio`, `Equity DD %`, `Trades` and `Custom`.
`Pass` comes from the agent logs and falls back to the row's index in the
cache when no log line names that symbol. `optimization_cache` also carries
`path`, `size_bytes`, `modified_at`, `expert`, `from_date` and `to_date`:

```json
{
  "job_id": "b05643…",
  "status": "completed",
  "broker": "darwinex",
  "account": "live",
  "report_name": "mode3-gbpcad-m15-last5y-rerun5.symbols.xml",
  "report_url": "/backtest/b05643…/report",
  "log_url": "/backtest/b05643…/log",
  "optimization_type": 3,
  "optimization_cache": {
    "name": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "pattern": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.*.opt",
    "build": "22",
    "cache_hash": "788ECDD113BA3097A58EF888EBEFF9CA",
    "row_count": 28,
    "symbol_component": "all_symbols",
    "period": "M15"
  },
  "optimization_results": [
    {
      "Pass": 21,
      "Symbol": "GBPJPY",
      "Result": 1657.54,
      "Profit": 657.54,
      "Profit Factor": 1.9,
      "Expected Payoff": 2.57,
      "Recovery Factor": 3.89,
      "Sharpe Ratio": 0.75,
      "Equity DD %": 11.22,
      "Trades": 256,
      "Custom": ""
    }
  ]
}
```

### `GET /backtest/{job_id}/report` & `/log`

`/report` streams the raw MT5 report: the HTML report for backtests, the XML
spreadsheet export for optimizations (the `.symbols.xml` header export for
mode `3`). It answers `404` until MT5 has written the report.

`/log` streams `run.log`, the stdout/stderr of `terminal64.exe` (usually
sparse). It is created when the terminal launches, so it is readable while the
job runs and answers `404` before that (and for a job that failed or was
cancelled before launch). The MT5 terminal and tester journals are in
[`/tail`](#get-backtestjob_idtail).

### `GET /backtest/{job_id}/tail`

Return the live diagnostic guts for queued, running, or finished jobs as JSON.
`?lines=N` controls the terminal/tester journal depth and is clamped to
`10..1000` (default `200`). The response includes `job_id`, `status`,
`started_at`, `finished_at`, `error`, `terminal_log`, `tester_log`, the
selected journal filenames (`terminal_log_file`, `tester_log_file`), and
`run_log`, which is always the last 50 lines of `run.log` whatever `lines` says.

### `POST /backtest/{job_id}/cancel`

Stop a job and mark it `cancelled`. Safe to repeat.

| Job | What happens | Answer |
| --- | --- | --- |
| unknown | nothing | `404`, `code: JOB_NOT_FOUND` |
| another terminal's (asked on the wrong instance) | nothing | `409`, `code: JOB_NOT_OWNED` |
| `completed`, `failed` or `cancelled` | nothing | `200`, the job as it is |
| `queued` | dropped before it launches; a run already on this terminal is not touched | `200` |
| `running` | this terminal's `terminal64.exe`, its `metatester64.exe` agents and any stuck LiveUpdate updater are killed; the next job starts only once they are gone | `200`, or `202` if the worker is still stopping after 40 s |

The answer is the job's status payload. A job this call cancelled carries
`error: "Cancelled by request"` and also gets `killed` (processes
signalled) and `stopped` (the worker has let go of the terminal); a job that
was already finished comes back without them. `cancelled` is final: a write
from the worker that is still winding down cannot turn it back into `failed` or
`completed`. A cancelled job usually has no report: `/report` serves one only
if MT5 had already written it when the cancel landed.

A job's worker can no longer die and leave the job `running`: any exception
outside the run's own error handling fails the job with `Backtest worker crashed
before the run finished: ...`, and a state file that cannot be written on the VM
share (WinError 5) is retried and then kept in memory instead of ending the run.

### Scheduled reboots

`GET /busy` lists every queued or running backtest of this terminal, and a
cancelled one whose worker is still stopping the tester processes, so a
scheduled VM reboot waits for them (up to `reboot_max_postpone`, default 360
minutes, see
[`scripts/reboot_guard.py`](https://github.com/psyb0t/mt5-httpapi/blob/master/scripts/reboot_guard.py)).
While the reboot is about to happen, every `POST` here (`/backtest`,
`build-ini`, `build-set`, `cancel`) answers `503` with
`code: REBOOT_PENDING` and `Retry-After: 120`.

### Worked example

```bash
export URL=http://127.0.0.1:8888/darwinex/tester
export TOK=changeme-mt5-httpapi-token

# 1. Build INI for a 5-year NZDJPY M15 open-prices run with 5 ms latency.
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"symbol":"NZDJPY","timeframe":"M15","expert":"EA Studio NZDJPY M15 1615044595.ex5","last_years":5,"modelling":"open-prices","latency_ms":5,"expert_parameters":"ea studio nzdjpy m15 1615044595.set"}' \
  > tester.ini

# 2. Submit using a host-managed expert + set already sitting in assets/.
JOB=$(curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini" \
  -F "expert_name=EA Studio NZDJPY M15 1615044595.ex5" \
  -F "set_name=ea studio nzdjpy m15 1615044595.set" \
  | jq -r .job_id)

# 3. Poll until done.
while :; do
  STATUS=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB" | jq -r .status)
  echo "$STATUS"; [[ "$STATUS" == completed || "$STATUS" == failed || "$STATUS" == cancelled ]] && break
  sleep 30
done

# 4. Fetch the report.
curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB/report" -o report.htm
```

### Optimization example

This assumes you already created a `.set` file in MT5 with optimization ranges
enabled and placed it in `assets/sets/` or plan to upload it inline.

```bash
export URL=http://127.0.0.1:8888/darwinex/tester
export TOK=changeme-mt5-httpapi-token

# Build the INI, submit the multipart job, poll to completion, then print both
# the parsed API summary and the first rows from the raw MT5 XML report.
tmp_ini=$(mktemp) && \
job_json=$(mktemp) && \
trap 'rm -f "$tmp_ini" "$job_json"' EXIT && \
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"GBPCAD","timeframe":"M15","expert":"EA Studio GBPCAD M15 1615044595.ex5","last_years":1,"modelling":"open-prices","expert_parameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set","optimization":1,"optimization_criterion":0,"report_name":"gbpcad-m15-last1y-openprices-opt"}' \
  > "$tmp_ini" && \
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@$tmp_ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "top_passes=20" \
  > "$job_json" && \
JOB=$(jq -r '.job_id' "$job_json") && \
echo "Submitted job: $JOB" && \
while :; do \
  STATUS_JSON=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB") && \
  STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.status') && \
  echo "Status: $STATUS" && \
  [[ "$STATUS" == completed || "$STATUS" == failed || "$STATUS" == cancelled ]] && break; \
  sleep 10; \
done && \
echo && echo "Final API summary:" && \
printf '%s\n' "$STATUS_JSON" | jq '{job_id,status,exit_code,duration_seconds,optimization_results}' && \
echo && echo "Report preview:" && \
curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB/report" \
  | grep -E '(<Row>|<Cell><Data ss:Type="String">|<Cell><Data ss:Type="Number">|<Cell ss:StyleID="[^"]+"><Data ss:Type="Number">)' \
  | head -n 60
```

Notes:

- Use a terminal configured with `mode: backtest`, not a live terminal namespace.
- Optimization results depend on the ranges encoded in the `.set` file. If no ranges are enabled in MT5, optimization is not meaningful.
- `optimization_results` is a convenience summary. For modes `1` and `2`, the raw XML at `/report` remains the full source of truth. For mode `3`, the parsed `.opt` cache plus `optimization_cache` metadata are the best debugging source because `/report` is the MT5 `.symbols.xml` header export.
- If a metric you expect is missing from `optimization_results` in mode `1` or `2`, first check the raw XML report. For XML rows the API preserves MT5's exported columns rather than remapping them to a fixed schema; mode `3` cache rows have the fixed column set listed above.
