# Backtesting without babysitting MT5

Build Strategy Tester files, launch the job, watch it run, and pull the reports back over HTTP instead of spending your life clicking around inside MT5.

## Contents

- [Backtest API](#backtest-api)
- [Build a set file](#post-backtestbuild-set)
- [Asset sources](#asset-sources)
- [Regenerate the warm-up expert](#regenerating-mt5systemwarmupex5)
- [Build an INI](#post-backtestbuild-ini)
- [Submit a job](#post-backtest)
- [Poll job status](#get-backtestjobid)
- [Download report and log](#get-backtestjobidreport--log)
- [Read live diagnostic tails](#get-backtestjobidtail)
- [Worked example](#worked-example)
- [Optimization example](#optimization-example)
- [Optimization guide](backtest-optimization.md)
- [Multi-VM scaling](multi-vm-setup.md)

## Backtest API

This is the whole Strategy Tester pipeline over HTTP: build the ugly MT5 files, submit them, poll the job, then grab the useful shit when it finishes.
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
| GET | `/backtest/<jobId>` | Poll job state and parsed results. |
| GET | `/backtest/<jobId>/report` | Download the raw MT5 report. |
| GET | `/backtest/<jobId>/log` | Download the terminal log. |
| GET | `/backtest/<jobId>/tail` | Read live diagnostic tails while a job runs. |

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
agent logs, and exposes the discovered cache artifact in `optimizationCache`.
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

That example matches the structure of the bundled preset under `assets/sets/`:
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

The response is `text/plain` `.set` content ready to save or upload.

Only one tester runs at a time per API process (serialized by an internal lock);
additional submissions queue.

If the API restarts while a job is queued or running, startup recovery marks
that orphaned job as `failed` with `API restarted before completion`. Recovery
runs in a background thread so a large job history can never delay the API from
serving, and only inspects jobs touched within the last `BACKTEST_SWEEP_LOOKBACK`
(default `24h`). Completed/failed job state and staging dirs are pruned once
older than `BACKTEST_JOB_RETENTION` (default `30d`), so job status/report/log
URLs for pruned jobs return 404 once past that window.

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
| `expert`           | yes      | filename ending in `.ex5`                          |
| `fromDate`+`toDate`| one of   | `YYYY-MM-DD`                                       |
| `lastYears`        | one of   | integer; window ends today UTC                     |
| `lastDays`         | one of   | integer                                            |
| `modelling`        | no       | `every-tick` `1m-ohlc` `open-prices` `real-ticks`  |
| `latencyMs`        | no       | integer milliseconds → `ExecutionMode`             |
| `deposit`          | no       | default `10000`                                    |
| `currency`         | no       | default `USD`                                      |
| `leverage`         | no       | default `100`, written as `1:N`                    |
| `expertParameters` | no       | `.set` filename                                    |
| `optimization`     | no       | `0` off, `1` slow complete, `2` genetic, `3` Market Watch symbols |
| `optimizationCriterion` | no  | `0..7`; default `0` (max balance)                 |
| `forwardMode`      | no       | `0..4`; default `0`                               |
| `visual`           | no       | truthy enables visual tester mode; default off    |
| `reportName`       | no       | default `backtest-report.htm` for backtests, `optimization-report.xml` for optimizations |

Returns `text/plain` with the generated INI.

Example JSON for an optimization INI:

```json
{
  "symbol": "GBPUSD",
  "timeframe": "M15",
  "expert": "MyEA.ex5",
  "lastYears": 3,
  "modelling": "open-prices",
  "expertParameters": "myea-optimizer.set",
  "optimization": 2,
  "optimizationCriterion": 5,
  "reportName": "gbpusd-m15-sharpe-search"
}
```

### `POST /backtest`

Multipart form fields:

| Field          | Required | Notes                                                          |
| -------------- | -------- | -------------------------------------------------------------- |
| `ini`          | yes      | INI file (file upload)                                         |
| `expert`       | one of   | `.ex5` upload                                                  |
| `expert_name`  | one of   | filename in `assets/experts/`                                  |
| `set`          | no       | `.set` upload                                                  |
| `set_name`     | no       | filename in `assets/sets/`                                     |
| `topPasses`    | no       | For optimization jobs, keep the top `1..500` parsed XML passes in the status payload. Default `50`. |
| `timeout`      | no       | Duration string override (`"30m"`, `"6h"`, `"3h30m"`). Defaults to `backtest_timeout` from `config.yaml`, then hardcoded `6h`. |

Responds `202 Accepted` with `Retry-After` header and the queued job payload:

```json
{
  "jobId": "b3f7…",
  "status": "queued",
  "broker": "darwinex",
  "account": "live",
  "submittedAt": "2026-05-12T10:00:00Z",
  "statusUrl": "/backtest/b3f7…",
  "reportUrl": "/backtest/b3f7…/report",
  "logUrl": "/backtest/b3f7…/log",
  "pollAfterSeconds": 60,
  "optimizationType": 0,
  "optimizationResults": null,
  "optimizationCache": null,
  "queuePosition": 1
}
```

`[Common]` `Login` / `Password` / `Server` in the uploaded INI are always
overwritten with the credentials from `config.yaml` for the request's
broker/account. The expert path is rewritten to `Uploaded\<basename>` and the
set file is namespaced per job to avoid collisions.

### `GET /backtest/{jobId}`

Status payload. `status` ∈ `queued` `running` `completed` `failed`. When
completed, includes a `summary` object parsed from the HTML report
(`netProfit`, `profitFactor`, `recoveryFactor`, `expectedPayoff`, `sharpeRatio`,
`maxDrawdown`, `totalTrades`, `profitTrades`, `lossTrades`, …).

For optimization jobs, the payload instead includes:

- `optimizationType` — the submitted MT5 optimization mode (`1`, `2`, or `3`)
- `optimizationResults` — a parsed top-N list sorted by `Result` descending
- `optimizationCache` — cache artifact metadata when results came from an MT5 `.opt` cache file

Result source depends on the submitted mode:

- Modes `1` and `2` parse the MT5 XML spreadsheet report first-class, and only use cache parsing if an `.opt` cache is available for the same job.
- Mode `3` parses the MT5 tester cache first-class because the `.symbols.xml` report does not contain the optimization rows.

The API keeps the MT5 column names as-is. If the XML export includes columns
such as `Profit`, `Profit Factor`, `Expected Payoff`, `Drawdown`,
`Recovery Factor`, `Sharpe Ratio`, or optimized input names, those same fields
appear in each `optimizationResults` row.

Example optimization status payload:

```json
{
  "jobId": "8c2a…",
  "status": "completed",
  "broker": "darwinex",
  "account": "tester",
  "reportName": "gbpusd-m15-sharpe-search.xml",
  "reportUrl": "/backtest/8c2a…/report",
  "logUrl": "/backtest/8c2a…/log",
  "optimizationType": 2,
  "optimizationCache": null,
  "optimizationResults": [
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

Example mode-3 optimization payload:

```json
{
  "jobId": "b05643…",
  "status": "completed",
  "broker": "darwinex",
  "account": "live",
  "reportName": "mode3-gbpcad-m15-last5y-rerun5.symbols.xml",
  "reportUrl": "/backtest/b05643…/report",
  "logUrl": "/backtest/b05643…/log",
  "optimizationType": 3,
  "optimizationCache": {
    "name": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.22.788ECDD113BA3097A58EF888EBEFF9CA.opt",
    "pattern": "EA Studio GBPCAD M15 1615044595.all_symbols.M15.20210525.20260525.*.opt",
    "build": "22",
    "cacheHash": "788ECDD113BA3097A58EF888EBEFF9CA",
    "rowCount": 28,
    "symbolComponent": "all_symbols",
    "period": "M15"
  },
  "optimizationResults": [
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

### `GET /backtest/{jobId}/report` & `/log`

Stream the raw report and terminal log file. Backtests return the MT5 HTML
report. Optimizations return the MT5 XML spreadsheet export. `404` until the
job finishes.

### `GET /backtest/{jobId}/tail`

Return the live diagnostic guts for queued, running, or finished jobs as JSON.
`?lines=N` controls the terminal/tester journal depth and is clamped to
`10..1000` (default `200`). The response includes `runLog`, `terminalLog`,
`testerLog`, status/timestamps, and the selected journal filenames.

### Worked example

```bash
export URL=http://127.0.0.1:8888/darwinex/live
export TOK=changeme-mt5-httpapi-token

# 1. Build INI for a 5-year NZDJPY M15 open-prices run with 5 ms latency.
curl -sS -X POST "$URL/backtest/build-ini" \
  -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"symbol":"NZDJPY","timeframe":"M15","expert":"EA Studio NZDJPY M15 1615044595.ex5","lastYears":5,"modelling":"open-prices","latencyMs":5,"expertParameters":"ea studio nzdjpy m15 1615044595.set"}' \
  > tester.ini

# 2. Submit using a host-managed expert + set already sitting in assets/.
JOB=$(curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@tester.ini" \
  -F "expert_name=EA Studio NZDJPY M15 1615044595.ex5" \
  -F "set_name=ea studio nzdjpy m15 1615044595.set" \
  | jq -r .jobId)

# 3. Poll until done.
while :; do
  STATUS=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB" | jq -r .status)
  echo "$STATUS"; [[ "$STATUS" == completed || "$STATUS" == failed ]] && break
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
  -d '{"symbol":"GBPCAD","timeframe":"M15","expert":"EA Studio GBPCAD M15 1615044595.ex5","lastYears":1,"modelling":"open-prices","expertParameters":"ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set","optimization":1,"optimizationCriterion":0,"reportName":"gbpcad-m15-last1y-openprices-opt"}' \
  > "$tmp_ini" && \
curl -sS -X POST "$URL/backtest" \
  -H "Authorization: Bearer $TOK" \
  -F "ini=@$tmp_ini;filename=tester.ini" \
  -F "expert_name=EA Studio GBPCAD M15 1615044595.ex5" \
  -F "set_name=ea studio gbpcad m15 1615044595.take-profit-opt-80-92-step4.set" \
  -F "topPasses=20" \
  > "$job_json" && \
JOB=$(jq -r '.jobId' "$job_json") && \
echo "Submitted job: $JOB" && \
while :; do \
  STATUS_JSON=$(curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB") && \
  STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.status') && \
  echo "Status: $STATUS" && \
  [[ "$STATUS" == completed || "$STATUS" == failed ]] && break; \
  sleep 10; \
done && \
echo && echo "Final API summary:" && \
printf '%s\n' "$STATUS_JSON" | jq '{jobId,status,exitCode,durationSeconds,optimizationResults}' && \
echo && echo "Report preview:" && \
curl -sS -H "Authorization: Bearer $TOK" "$URL/backtest/$JOB/report" \
  | grep -E '(<Row>|<Cell><Data ss:Type="String">|<Cell><Data ss:Type="Number">|<Cell ss:StyleID="[^"]+"><Data ss:Type="Number">)' \
  | head -n 60
```

Notes:

- Use a terminal configured with `mode: backtest`, not a live terminal namespace.
- Optimization results depend on the ranges encoded in the `.set` file. If no ranges are enabled in MT5, optimization is not meaningful.
- `optimizationResults` is a convenience summary. For modes `1` and `2`, the raw XML at `/report` remains the full source of truth. For mode `3`, the parsed `.opt` cache plus `optimizationCache` metadata are the best debugging source because `/report` is the MT5 `.symbols.xml` header export.
- If a metric you expect is missing from `optimizationResults`, first check the raw XML report. The API preserves MT5's exported columns rather than remapping them to a fixed schema.
